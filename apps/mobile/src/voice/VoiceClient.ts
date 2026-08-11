/**
 * VoiceClient — Phase 3.2.
 *
 * Bidirectional bridge between the on-device audio I/O and the
 * voice-gateway (services/voice-gateway/main.py). One instance per
 * authenticated user; reconnects with the same `session_id` so the
 * server's `VoiceSession` resumes its in-memory state (focus pins,
 * conversation history).
 *
 * Audio path:
 *     AudioRecorder.onFrame  → ws.send(ArrayBuffer)
 *     ws.binaryMessage        → AudioPlayer.feed(Uint8Array)
 *
 * Control path:
 *     startPTT  → send {"type":"ptt_start"}, recorder.start()
 *     endPTT    → recorder.stop(), send {"type":"ptt_end"}
 *     cancel    → send {"type":"cancel"}, player.flush()
 *     focus(id) → send {"type":"focus", "listing_id":id|"document_id":id}
 *
 * Inbound events match the wire format established in P2.3:
 *     hello / resumed / transcript_partial / transcript_final
 *     reply_text / agent_event / cancel / run_end / error
 */
import { AudioRecorder, type AudioFrame } from './AudioRecorder';
import { AudioPlayer } from './AudioPlayer';

// ---------------------------------------------------------------------------
// Events — must match the server's outbound `send_text` payloads.
// ---------------------------------------------------------------------------
export type VoiceEvent =
  | { type: 'hello'; session_id: string; tenant_id: string }
  | { type: 'resumed'; session_id: string }
  | { type: 'transcript_partial'; text: string }
  | { type: 'transcript_final'; text: string }
  | { type: 'reply_text'; text: string }
  | { type: 'agent_event'; event: AgentTraceStep }
  | { type: 'run_end'; event: AgentTraceStep }
  | { type: 'tts_error'; message: string; voice_id?: string | null }
  | { type: 'cancel'; reason?: string }
  | { type: 'error'; message: string };

export type AgentTraceStep = {
  kind: 'agent_start' | 'agent_tool' | 'tool_result' | 'agent_end'
      | 'text_delta' | 'run_end' | 'error';
  agent: string;
  ts_ms: number;
  detail: Record<string, unknown>;
  run_id?: string;
};

export type VoiceClientState =
  | 'idle' | 'connecting' | 'connected' | 'recording'
  | 'reconnecting' | 'closed' | 'error';

// ---------------------------------------------------------------------------
// Construction options
// ---------------------------------------------------------------------------
export interface VoiceClientOptions {
  /** Base wss URL of the gateway, e.g. `wss://voice.casaorquesta.mx`. */
  url: string;
  tenantId: string;
  userId: string;
  authToken: string;

  /** Audio I/O injectors — VoiceProvider wires the real ones. */
  recorder?: AudioRecorder | null;
  player?: AudioPlayer | null;

  /** Resume an existing gateway session (per client thread). */
  initialSessionId?: string | null;

  /** Outbound event callbacks. */
  onEvent: (event: VoiceEvent) => void;
  onStateChange?: (state: VoiceClientState) => void;
  onOpen?: () => void;
  onClose?: () => void;
  /** Fired when the first TTS audio frame arrives for a turn. */
  onAudioStart?: () => void;
  /** Fired on every inbound TTS audio frame (including trailing REST chunks). */
  onAudioChunk?: () => void;

  /** WebSocket factory (injectable for tests). */
  socketFactory?: (url: string, protocols?: string | string[]) => WebSocket;

  /** Reconnect tuning. */
  maxReconnectAttempts?: number;
  reconnectBaseMs?: number;
  reconnectMaxMs?: number;
}

const DEFAULTS = {
  maxReconnectAttempts: 8,
  reconnectBaseMs: 500,
  reconnectMaxMs: 5000,
};

const CONNECT_TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// VoiceClient
// ---------------------------------------------------------------------------
export class VoiceClient {
  private ws: WebSocket | null = null;
  private opts: Required<Pick<VoiceClientOptions,
    | 'url' | 'tenantId' | 'userId' | 'authToken' | 'onEvent'>>
    & VoiceClientOptions;

  private sessionId: string | null = null;
  private state: VoiceClientState = 'idle';
  private reconnectAttempts = 0;
  private intentionallyClosed = false;
  private connectPromise: Promise<void> | null = null;
  private pttActive = false;
  private audioStartedThisTurn = false;

  private recorder: AudioRecorder | null;
  private player: AudioPlayer | null;
  private socketFactory: (url: string, protocols?: string | string[]) => WebSocket;

  constructor(opts: VoiceClientOptions) {
    this.opts = {
      maxReconnectAttempts: opts.maxReconnectAttempts ?? DEFAULTS.maxReconnectAttempts,
      reconnectBaseMs:     opts.reconnectBaseMs     ?? DEFAULTS.reconnectBaseMs,
      reconnectMaxMs:      opts.reconnectMaxMs      ?? DEFAULTS.reconnectMaxMs,
      ...opts,
    };
    this.recorder = opts.recorder ?? null;
    this.player = opts.player ?? null;
    this.sessionId = opts.initialSessionId ?? null;
    this.socketFactory = opts.socketFactory ??
      ((url: string, protocols?: string | string[]) => new WebSocket(url, protocols));
  }

  // ----- public API -----
  get currentState(): VoiceClientState { return this.state; }
  get currentSessionId(): string | null { return this.sessionId; }

  async connect(): Promise<void> {
    if (this.ws?.readyState === WebSocket.OPEN) return;
    if (this.connectPromise) return this.connectPromise;

    this.intentionallyClosed = false;
    this.connectPromise = new Promise<void>((resolve, reject) => {
      this._setState('connecting');
      const url = this._buildUrl();
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error('voice gateway connect timeout'));
      }, CONNECT_TIMEOUT_MS);

      const finish = (err?: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (err) reject(err);
        else resolve();
      };

      try {
        const ws = this.socketFactory(url);
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => {
          this.reconnectAttempts = 0;
          this._setState('connected');
          this._installRecorderBridge();
          this.opts.onOpen?.();
          finish();
        };
        ws.onmessage = (event: MessageEvent) => {
          if (typeof event.data === 'string') {
            try {
              const parsed = JSON.parse(event.data) as VoiceEvent;
              this._handleControl(parsed);
              this.opts.onEvent(parsed);
            } catch {
              this.opts.onEvent({ type: 'error', message: 'malformed JSON event' });
            }
            return;
          }
          void this._handleBinaryMessage(event.data);
        };
        ws.onclose = () => {
          this.opts.onClose?.();
          if (!settled) {
            finish(new Error('voice gateway closed before ready'));
          }
          if (this.intentionallyClosed) {
            this._setState('closed');
            return;
          }
          this._scheduleReconnect();
        };
        ws.onerror = () => {
          // Transient drops during long TTS are common on simulator; reconnect
          // handles recovery — don't latch the mic button to error.
          if (!settled) {
            finish(new Error('websocket error'));
            return;
          }
          if (this.state === 'connecting') {
            this.opts.onEvent({ type: 'error', message: 'websocket error' });
          }
        };
        this.ws = ws;
      } catch (e) {
        finish(e instanceof Error ? e : new Error(String(e)));
      }
    }).finally(() => {
      this.connectPromise = null;
    });

    try {
      await this.connectPromise;
    } catch (e) {
      this._setState('error');
      this.opts.onEvent({
        type: 'error',
        message: e instanceof Error ? e.message : String(e),
      });
      throw e;
    }
  }

  async startPTT(): Promise<void> {
    await this.connect();
    if (!this._send({ type: 'ptt_start' })) {
      this.opts.onEvent({ type: 'error', message: 'voice gateway not connected' });
      return;
    }
    this._resetAudioTurn();
    this.pttActive = true;
    this._setState('recording');
    if (this.recorder) {
      try { await this.recorder.start(); }
      catch (e) {
        this.pttActive = false;
        this._setState('connected');
        this.opts.onEvent({ type: 'error', message: `mic start failed: ${String(e)}` });
      }
    }
  }

  async endPTT(): Promise<void> {
    if (!this.pttActive) return;
    this.pttActive = false;
    // Stop + flush PCM first, then tell the gateway the utterance ended.
    if (this.recorder) {
      try { await this.recorder.stop(); }
      catch { /* ignore — double-stop is benign */ }
    }
    this._send({ type: 'ptt_end' });
    this._setState('connected');
  }

  cancel(): void {
    this.pttActive = false;
    this._resetAudioTurn();
    this._send({ type: 'cancel' });
    if (this.player) void this.player.flush();
  }

  focusListing(listingId: string): void {
    this._send({ type: 'focus', listing_id: listingId });
  }

  focusDocument(documentId: string): void {
    this._send({ type: 'focus', document_id: documentId });
  }

  /** Switch to another thread's voice session (closes WS and reconnects). */
  async switchSession(sessionId: string | null): Promise<void> {
    this.pttActive = false;
    this.intentionallyClosed = true;
    try {
      this.ws?.close();
    } catch {
      /* noop */
    }
    this.ws = null;
    this.connectPromise = null;
    this.sessionId = sessionId;
    this.intentionallyClosed = false;
    this.reconnectAttempts = 0;
    await this.connect();
  }

  setContext(payload: {
    conversation_id?: string;
    welcome_sent?: boolean;
    client_role?: 'buyer' | 'seller';
    focus_listing_id?: string | null;
    focus_document_id?: string | null;
    focus_person_id?: string | null;
    focus_person_kind?: 'buyer' | 'collaborator' | 'broker' | null;
    focus_person_name?: string | null;
    client_profile?: Record<string, unknown> | null;
  }): void {
    this._send({ type: 'set_context', ...payload });
  }

  /** MVP quickSend — inject a typed follow-up without PTT. */
  sendUserMessage(
    text: string,
    clientProfile?: Record<string, unknown> | null,
  ): void {
    const trimmed = text.trim();
    if (!trimmed) return;
    const payload: Record<string, unknown> = { type: 'user_message', text: trimmed };
    if (clientProfile !== undefined) {
      payload.client_profile = clientProfile;
    }
    this._send(payload);
  }

  focusPerson(
    personId: string,
    meta?: { kind?: 'buyer' | 'collaborator' | 'broker'; name?: string },
  ): void {
    this._send({
      type: 'focus',
      person_id: personId,
      person_kind: meta?.kind,
      person_name: meta?.name,
    });
    this.setContext({
      focus_person_id: personId,
      focus_person_kind: meta?.kind ?? null,
      focus_person_name: meta?.name ?? null,
    });
  }

  close(): void {
    this.intentionallyClosed = true;
    this.ws?.close();
    this.ws = null;
    if (this.recorder) void this.recorder.release();
    if (this.player) void this.player.close();
    this._setState('closed');
  }

  // ----- internals -----
  private _buildUrl(): string {
    const base = this.opts.url.replace(/\/$/, '');
    const resume = this.sessionId
      ? `/${encodeURIComponent(this.sessionId)}`
      : '';
    const auth = encodeURIComponent(this.opts.authToken);
    const tenant = encodeURIComponent(this.opts.tenantId);
    const user = encodeURIComponent(this.opts.userId);
    return `${base}/voice/${tenant}/${user}${resume}?token=${auth}`;
  }

  /**
   * Track session_id from the server's first event so a reconnect
   * uses the `/voice/{tenant}/{user}/{session_id}` resume route.
   */
  private _handleControl(ev: VoiceEvent): void {
    if (ev.type === 'hello' || ev.type === 'resumed') {
      this.sessionId = ev.session_id;
    } else if (ev.type === 'cancel') {
      this._resetAudioTurn();
      if (this.player) void this.player.flush();
    }
  }

  private _resetAudioTurn(): void {
    this.audioStartedThisTurn = false;
  }

  private _installRecorderBridge(): void {
    // Bridge is wired via constructor onFrame; kept for test overrides.
  }

  sendAudioFrame = async (frame: AudioFrame): Promise<void> => {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    try {
      this.ws.send(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
    } catch (e) {
      this.opts.onEvent({ type: 'error', message: `ws.send failed: ${String(e)}` });
    }
  };

  private async _handleBinaryMessage(data: unknown): Promise<void> {
    let bytes: Uint8Array | null = null;
    if (data instanceof ArrayBuffer) {
      bytes = new Uint8Array(data);
    } else if (typeof Blob !== 'undefined' && data instanceof Blob) {
      bytes = new Uint8Array(await data.arrayBuffer());
    } else if (data instanceof Uint8Array) {
      bytes = data;
    } else if (
      data !== null
      && typeof data === 'object'
      && 'buffer' in (data as ArrayBufferView)
    ) {
      const view = data as ArrayBufferView;
      bytes = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    }
    if (!bytes || bytes.byteLength === 0) return;
    if (!this.audioStartedThisTurn) {
      this.audioStartedThisTurn = true;
      this.opts.onAudioStart?.();
    }
    this.opts.onAudioChunk?.();
    if (this.player) await this.player.feed(bytes);
  }

  private _send(payload: Record<string, unknown>): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    try {
      this.ws.send(JSON.stringify(payload));
      return true;
    } catch {
      return false;
    }
  }

  private _scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.opts.maxReconnectAttempts!) {
      this._setState('error');
      return;
    }
    this._setState('reconnecting');
    const delay = Math.min(
      this.opts.reconnectBaseMs! * 2 ** this.reconnectAttempts,
      this.opts.reconnectMaxMs!,
    );
    this.reconnectAttempts += 1;
    setTimeout(() => { void this.connect(); }, delay);
  }

  private _setState(s: VoiceClientState): void {
    if (this.state === s) return;
    this.state = s;
    this.opts.onStateChange?.(s);
  }
}
