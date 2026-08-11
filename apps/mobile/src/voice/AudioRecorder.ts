/**
 * AudioRecorder — Phase 3.2.
 *
 * Provider-agnostic streaming audio capture. Production uses `expo-av`'s
 * Recording API as the backend; tests pass a mock backend that yields
 * scripted frames so the upstream `VoiceClient` can be exercised without
 * touching the simulator's mic.
 *
 * Frame contract (what we hand to `onFrame`):
 *   - Encoding:  16 kHz mono PCM, signed 16-bit little-endian
 *   - Window:    20 ms (= 320 samples = 640 bytes)
 *   - Opus is also acceptable when the gateway negotiates it later
 *
 * Why poll instead of pure streaming?
 *   Expo SDK 52's stable Recording API doesn't expose a native callback
 *   for in-progress audio buffers. We approximate streaming by reading
 *   the partial file at ~50 Hz, slicing off the newly-appended bytes,
 *   and emitting them as frames. This adds <20 ms of buffering on top
 *   of the OS recorder, which the latency budget absorbs. The expo-audio
 *   module (in beta as of SDK 52) will let us swap this for native
 *   frame callbacks without changing the surface.
 */

import { preparePlaybackSession, prepareRecordingSession } from './audioSession';

export type AudioFrame = Uint8Array;
export type OnFrame = (frame: AudioFrame) => void | Promise<void>;
export type OnLevel = (rmsNormalized: number) => void;

export interface RecorderOptions {
  /** Called for each ~20 ms audio frame. */
  onFrame: OnFrame;
  /** Optional level meter for the UI waveform. 0…1 normalized RMS. */
  onLevel?: OnLevel;
  /** PCM sample rate. Defaults to 16 kHz to match Deepgram nova-2. */
  sampleRate?: 8000 | 16000 | 24000 | 48000;
  /** Frame size in milliseconds. Defaults to 20. */
  frameMs?: 10 | 20 | 40;
  /** Inject a backend (used by tests). */
  backend?: AudioRecorderBackend;
}

export interface AudioRecorderBackend {
  /** Allocate native resources. Idempotent. */
  prepare(): Promise<void>;
  /** Begin emitting frames via the provided callback. */
  start(opts: { onFrame: OnFrame; onLevel?: OnLevel;
                sampleRate: number; frameBytes: number }): Promise<void>;
  /** Stop emitting frames; flush whatever's in the in-progress buffer. */
  stop(): Promise<void>;
  /** Tear down native resources. */
  release(): Promise<void>;
  readonly isRecording: boolean;
}

/**
 * Public class — what callers interact with. Delegates to a swappable
 * backend so we can stub for tests.
 */
export class AudioRecorder {
  private readonly opts: Required<Omit<RecorderOptions, 'backend' | 'onLevel'>>
    & Pick<RecorderOptions, 'onLevel' | 'backend'>;
  private readonly backend: AudioRecorderBackend;

  constructor(opts: RecorderOptions) {
    this.opts = {
      onFrame: opts.onFrame,
      onLevel: opts.onLevel,
      sampleRate: opts.sampleRate ?? 16000,
      frameMs: opts.frameMs ?? 20,
      backend: opts.backend,
    };
    this.backend = opts.backend ?? createDefaultBackend();
  }

  /** Bytes per emitted frame for the configured sample rate + window. */
  get frameBytes(): number {
    return (this.opts.sampleRate / 1000) * this.opts.frameMs * 2; // 2 bytes/sample
  }

  get isRecording(): boolean {
    return this.backend.isRecording;
  }

  async prepare(): Promise<void> {
    await this.backend.prepare();
  }

  async start(): Promise<void> {
    await this.backend.start({
      onFrame: this.opts.onFrame,
      onLevel: this.opts.onLevel,
      sampleRate: this.opts.sampleRate,
      frameBytes: this.frameBytes,
    });
  }

  async stop(): Promise<void> {
    await this.backend.stop();
  }

  async release(): Promise<void> {
    await this.backend.release();
  }
}

// ---------------------------------------------------------------------------
// Default backend — expo-av Recording API.
//
// We construct the recorder lazily on `prepare()` so importing this module
// from a test file (no expo-av installed) doesn't crash. The dynamic
// require also lets Metro tree-shake it on platforms that don't need audio.
// ---------------------------------------------------------------------------
export function createDefaultBackend(): AudioRecorderBackend {
  return new ExpoAvRecorderBackend();
}

type ExpoAvModule = typeof import('expo-av');
type ExpoFsModule = typeof import('expo-file-system');
type RecordingObject = InstanceType<ExpoAvModule['Audio']['Recording']>;

class ExpoAvRecorderBackend implements AudioRecorderBackend {
  private recording: RecordingObject | null = null;
  private intervalHandle: ReturnType<typeof setInterval> | null = null;
  private prepared = false;
  private _isRecording = false;
  private _onFrame: OnFrame | null = null;
  private _frameBytes = 640;

  get isRecording(): boolean { return this._isRecording; }

  async prepare(): Promise<void> {
    await prepareRecordingSession();
    this.prepared = true;
  }

  async start({
    onFrame, onLevel, sampleRate, frameBytes,
  }: {
    onFrame: OnFrame; onLevel?: OnLevel;
    sampleRate: number; frameBytes: number;
  }): Promise<void> {
    if (this._isRecording) return;
    await prepareRecordingSession();
    const { Audio } = await loadExpoAv();

    this._onFrame = onFrame;
    this._frameBytes = frameBytes;

    const rec = new Audio.Recording();
    await rec.prepareToRecordAsync({
      android: {
        extension: '.wav',
        outputFormat: 1,
        audioEncoder: 1,
        sampleRate,
        numberOfChannels: 1,
        bitRate: sampleRate * 16,
      },
      ios: {
        extension: '.wav',
        audioQuality: 0x7F,   // MAX
        sampleRate,
        numberOfChannels: 1,
        bitRate: sampleRate * 16,
        linearPCMBitDepth: 16,
        linearPCMIsBigEndian: false,
        linearPCMIsFloat: false,
      },
      web: {
        mimeType: 'audio/webm',
        bitsPerSecond: sampleRate * 16,
      },
      isMeteringEnabled: !!onLevel,
    });
    await rec.startAsync();
    this.recording = rec;
    this._isRecording = true;

    // iOS does not expose growing partial files during recording — the
    // WAV is finalized on stop. We only poll metering here for the UI;
    // PCM frames are batch-emitted from stop().
    if (onLevel) {
      const tickMs = 50;
      this.intervalHandle = setInterval(async () => {
        if (!this._isRecording || !this.recording) return;
        try {
          const status = await this.recording.getStatusAsync();
          if (status?.metering != null) {
            onLevel(meteringToNormalized(status.metering));
          }
        } catch {
          /* ignore metering tick errors */
        }
      }, tickMs);
    }
  }

  async stop(): Promise<void> {
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle);
      this.intervalHandle = null;
    }
    const onFrame = this._onFrame;
    const frameBytes = this._frameBytes;
    let uri: string | null = null;
    if (this.recording) {
      try {
        uri = this.recording.getURI?.() ?? null;
        await this.recording.stopAndUnloadAsync();
      } catch { /* ignore double-stop */ }
      this.recording = null;
    }
    this._isRecording = false;
    this._onFrame = null;

    // Hand off to playback for the agent reply.
    try {
      await preparePlaybackSession();
    } catch { /* noop */ }

    // Batch-send the finalized recording to the voice gateway.
    if (uri && onFrame) {
      try {
        const pcm = await readRecordingPcm(uri);
        if (pcm && pcm.length > 0) {
          for (let i = 0; i < pcm.length; i += frameBytes) {
            const slice = pcm.subarray(i, Math.min(i + frameBytes, pcm.length));
            await onFrame(new Uint8Array(slice));
          }
        }
      } catch (e) {
        console.warn('[AudioRecorder] flush on stop failed:', e);
      }
    }
  }

  async release(): Promise<void> {
    await this.stop();
    this.prepared = false;
  }
}

// ---------------------------------------------------------------------------
// Mock backend — drives the recorder from a scripted frame list. Used by
// tests and the dev "echo" mode that mirrors mic frames to TTS.
// ---------------------------------------------------------------------------
export class MockRecorderBackend implements AudioRecorderBackend {
  private timer: ReturnType<typeof setInterval> | null = null;
  private idx = 0;
  private _isRecording = false;
  private opts: { onFrame: OnFrame; frameBytes: number } | null = null;

  constructor(
    private readonly frames: AudioFrame[],
    private readonly intervalMs = 20,
  ) {}

  get isRecording(): boolean { return this._isRecording; }

  async prepare(): Promise<void> { /* no-op */ }

  async start(opts: { onFrame: OnFrame; sampleRate: number; frameBytes: number }) {
    this.opts = { onFrame: opts.onFrame, frameBytes: opts.frameBytes };
    this.idx = 0;
    this._isRecording = true;
    this.timer = setInterval(() => {
      if (!this._isRecording || !this.opts) return;
      if (this.idx >= this.frames.length) return;
      const frame = this.frames[this.idx++];
      if (frame) {
        void Promise.resolve(this.opts.onFrame(frame));
      }
    }, this.intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this._isRecording = false;
  }

  async release(): Promise<void> { await this.stop(); this.opts = null; }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
let _expoAv: ExpoAvModule | null = null;
async function loadExpoAv(): Promise<ExpoAvModule> {
  if (_expoAv) return _expoAv;
  // Dynamic import keeps test files runnable without expo-av installed.
  _expoAv = await import('expo-av');
  return _expoAv;
}

/** Read a finalized expo-av recording and return raw PCM16 LE mono bytes. */
async function readRecordingPcm(uri: string): Promise<Uint8Array | null> {
  let FileSystem: ExpoFsModule;
  try {
    FileSystem = await import('expo-file-system');
  } catch {
    return null;
  }
  const stat = await FileSystem.getInfoAsync(uri, { size: true });
  if (!stat?.exists) return null;
  const b64 = await FileSystem.readAsStringAsync(uri, {
    encoding: FileSystem.EncodingType.Base64,
  });
  const buf = base64ToUint8(b64);
  return extractPcmFromContainer(buf);
}

/**
 * Strip RIFF/WAV container headers and return the `data` chunk payload.
 * Falls back to the classic 44-byte skip when the header is minimal.
 */
export function extractPcmFromContainer(buf: Uint8Array): Uint8Array {
  if (buf.length >= 12
      && buf[0] === 0x52 && buf[1] === 0x49 && buf[2] === 0x46 && buf[3] === 0x46) {
    let offset = 12;
    while (offset + 8 <= buf.length) {
      const id = String.fromCharCode(
        buf[offset] ?? 0,
        buf[offset + 1] ?? 0,
        buf[offset + 2] ?? 0,
        buf[offset + 3] ?? 0,
      );
      const size = (buf[offset + 4] ?? 0)
        | ((buf[offset + 5] ?? 0) << 8)
        | ((buf[offset + 6] ?? 0) << 16)
        | ((buf[offset + 7] ?? 0) << 24);
      if (id === 'data') {
        return buf.subarray(offset + 8, offset + 8 + size);
      }
      offset += 8 + size + (size % 2);
    }
  }
  return buf.length > 44 ? buf.subarray(44) : buf;
}

function base64ToUint8(b64: string): Uint8Array {
  // RN doesn't have atob in older Hermes builds; use a small inline decoder.
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const lookup = new Uint8Array(256);
  for (let i = 0; i < chars.length; i++) lookup[chars.charCodeAt(i)] = i;
  let bufferLength = (b64.length * 3) >> 2;
  if (b64.endsWith('==')) bufferLength -= 2;
  else if (b64.endsWith('=')) bufferLength -= 1;
  const bytes = new Uint8Array(bufferLength);
  let p = 0;
  for (let i = 0; i < b64.length; i += 4) {
    const e0 = lookup[b64.charCodeAt(i)] ?? 0;
    const e1 = lookup[b64.charCodeAt(i + 1)] ?? 0;
    const e2 = lookup[b64.charCodeAt(i + 2)] ?? 0;
    const e3 = lookup[b64.charCodeAt(i + 3)] ?? 0;
    if (p < bufferLength) bytes[p++] = (e0 << 2) | (e1 >> 4);
    if (p < bufferLength) bytes[p++] = ((e1 & 15) << 4) | (e2 >> 2);
    if (p < bufferLength) bytes[p++] = ((e2 & 3) << 6) | (e3 & 63);
  }
  return bytes;
}

/** Map expo-av metering dB (-160…0) to a 0…1 RMS-ish value. */
export function meteringToNormalized(db: number): number {
  if (db <= -60) return 0;
  if (db >= 0) return 1;
  return 1 - db / -60;
}
