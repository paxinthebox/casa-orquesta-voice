/**
 * VoiceProvider — Phase 3.2.
 *
 * Owns the per-session AudioRecorder + AudioPlayer + VoiceClient triple
 * and exposes a small status-oriented hook to the UI:
 *
 *     const { status, transcriptPartial, transcriptFinal,
 *             startPTT, endPTT, cancel,
 *             rmsIn, rmsOut, micPermission } = useVoice();
 *
 * The wiring is lazy: we don't construct the recorder/player/client
 * until the user actually taps to talk. That keeps the screens render-
 * cheap and lets the consent gate intercept the first mic request.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { StyleSheet, View } from 'react-native';

import { useSession } from '@/state/SessionProvider';
import { useAgentTrace } from '@/state/agentTraceStore';
import { useCardsStore, extractRunResultsFromRunEnd } from '@/state/cardsStore';
import { useThreadsStore } from '@/state/threadsStore';
import { clientProfileToWire } from '@/utils/clientProfile';
import { AudioRecorder } from './AudioRecorder';
import {
  AudioPlayer,
  prepareRecordingSession,
  resolveTtsInputFormat,
} from './AudioPlayer';
import { speakOnDevice, stopDeviceSpeech } from './deviceSpeech';
import {
  VoiceClient,
  type VoiceClientState,
  type VoiceEvent,
} from './VoiceClient';
import {
  getMicPermissionStatus,
  requestMicPermission,
  type MicPermissionStatus,
} from './permissions';

export type VoiceUiStatus =
  | 'idle' | 'listening' | 'thinking' | 'speaking' | 'error';

type VoiceContextValue = {
  status: VoiceUiStatus;
  clientState: VoiceClientState;
  transcriptPartial: string;
  transcriptFinal: string;
  replyPartial: string;
  rmsIn: number;
  rmsOut: number;
  micPermission: MicPermissionStatus;
  startPTT: () => Promise<void>;
  endPTT: () => Promise<void>;
  cancel: () => void;
  ensureMicPermission: () => Promise<MicPermissionStatus>;
  bootstrapSession: () => Promise<void>;
  focusListing: (id: string) => void;
  focusDocument: (id: string) => void;
  focusPerson: (
    id: string,
    meta?: { kind?: 'buyer' | 'collaborator' | 'broker'; name?: string },
  ) => void;
  sendFollowUpMessage: (text: string, options?: {
    displayText?: string;
    clientProfile?: Record<string, unknown> | null;
    threadId?: string;
  }) => Promise<void>;
  onThreadChanged: () => Promise<void>;
  syncThreadContext: () => void;
};

const VoiceContext = createContext<VoiceContextValue | null>(null);

export function useVoice(): VoiceContextValue {
  const ctx = useContext(VoiceContext);
  if (!ctx) throw new Error('useVoice() must be called inside <VoiceProvider>.');
  return ctx;
}

const GATEWAY_URL =
  (process.env.EXPO_PUBLIC_VOICE_GATEWAY_URL as string | undefined) ??
  'ws://localhost:8010';

/** Never use system TTS unless explicitly opted in via env at build time. */
function deviceTtsFallbackEnabled(): boolean {
  return process.env.EXPO_PUBLIC_DEVICE_TTS_FALLBACK === '1';
}

const PLAYBACK_QUIET_MS = 450;
const PLAYBACK_FIRST_BYTE_WAIT_MS = 12_000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isRecoverableGatewayError(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes('no se escuch')
    || m.includes("couldn't hear")
    || m.includes('try again')
    || m.includes('recording not allowed')
    || m.includes('mic start failed')
  );
}

export function VoiceProvider({ children }: { children: React.ReactNode }) {
  const session = useSession();

  const clientRef = useRef<VoiceClient | null>(null);
  const recorderRef = useRef<AudioRecorder | null>(null);
  const playerRef = useRef<AudioPlayer | null>(null);

  const [status, setStatus] = useState<VoiceUiStatus>('idle');
  const [clientState, setClientState] = useState<VoiceClientState>('idle');
  const [partial, setPartial] = useState('');
  const [finalText, setFinalText] = useState('');
  const [replyPartial, setReplyPartial] = useState('');
  const [rmsIn, setRmsIn] = useState(0);
  const [rmsOut] = useState(0); // wired when assistant player exposes metering
  const [micPermission, setMicPermission] = useState<MicPermissionStatus>('undetermined');

  // Refresh permission status when the app focuses (P3.3 will hook AppState).
  useEffect(() => {
    let cancelled = false;
    void getMicPermissionStatus().then((s) => {
      if (!cancelled) setMicPermission(s);
    });
    return () => { cancelled = true; };
  }, []);

  // Tear-down on unmount.
  useEffect(() => {
    return () => {
      clientRef.current?.close();
      void recorderRef.current?.release();
      void playerRef.current?.close();
      clientRef.current = null;
      recorderRef.current = null;
      playerRef.current = null;
    };
  }, []);

  // Safety net: if the gateway never sends run_end/error, don't stay
  // stuck on "Searching…" forever (e.g. orchestrator hang).
  useEffect(() => {
    if (status !== 'thinking') return;
    const timer = setTimeout(() => setStatus('idle'), 90_000);
    return () => clearTimeout(timer);
  }, [status]);

  const applyTrace = useAgentTrace((s) => s.applyEvent);
  const ingestCards = useCardsStore((s) => s.ingestEvent);
  const ingestSearchResults = useCardsStore((s) => s.ingestSearchResults);
  const appendMessage = useCardsStore((s) => s.appendMessage);
  const commitPendingTimelineCards = useCardsStore((s) => s.commitPendingTimelineCards);
  const syncFromActiveThread = useCardsStore((s) => s.syncFromActiveThread);
  const activeThreadId = useThreadsStore((s) => s.activeThreadId);
  const activeClientRole = useThreadsStore((s) => {
    const thread = s.threads.find((t) => t.id === s.activeThreadId);
    return thread?.clientRole ?? 'buyer';
  });
  const getActiveThread = useThreadsStore((s) => s.getActiveThread);
  const setVoiceSessionId = useThreadsStore((s) => s.setVoiceSessionId);
  const markWelcomeSent = useThreadsStore((s) => s.markWelcomeSent);
  const replyRef = useRef('');
  const turnThreadRef = useRef<string | null>(null);
  const audioReceivedRef = useRef(false);
  const ttsFailedRef = useRef(false);
  const turnAwaitingPlaybackRef = useRef(false);
  const playbackDrainTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const playbackDrainDoneRef = useRef<(() => void) | null>(null);
  const playbackDrainWaitRef = useRef<Promise<void> | null>(null);
  const sessionBootstrappedRef = useRef(false);
  const ensureClientPromiseRef = useRef<Promise<VoiceClient | null> | null>(null);
  const lastAssistantAppendRef = useRef<{ text: string; at: number } | null>(null);
  const prevThreadIdRef = useRef<string | null>(null);
  const prevRoleRef = useRef<'buyer' | 'seller' | null>(null);

  useEffect(() => {
    replyRef.current = replyPartial;
  }, [replyPartial]);

  const clearPlaybackDrainTimer = useCallback(() => {
    if (playbackDrainTimerRef.current) {
      clearTimeout(playbackDrainTimerRef.current);
      playbackDrainTimerRef.current = null;
    }
  }, []);

  const armPlaybackDrain = useCallback(() => {
    if (!turnAwaitingPlaybackRef.current) return;
    clearPlaybackDrainTimer();
    if (!playbackDrainWaitRef.current) {
      playbackDrainWaitRef.current = new Promise<void>((resolve) => {
        playbackDrainDoneRef.current = resolve;
      });
    }
    playbackDrainTimerRef.current = setTimeout(() => {
      playbackDrainTimerRef.current = null;
      void (async () => {
        try {
          await playerRef.current?.drain();
        } finally {
          playbackDrainDoneRef.current?.();
          playbackDrainDoneRef.current = null;
          playbackDrainWaitRef.current = null;
        }
      })();
    }, PLAYBACK_QUIET_MS);
  }, [clearPlaybackDrainTimer]);

  const waitForTurnPlayback = useCallback(async () => {
    const deadline = Date.now() + PLAYBACK_FIRST_BYTE_WAIT_MS;
    while (!audioReceivedRef.current && Date.now() < deadline) {
      await sleep(50);
    }
    armPlaybackDrain();
    if (playbackDrainWaitRef.current) {
      await Promise.race([
        playbackDrainWaitRef.current,
        sleep(PLAYBACK_FIRST_BYTE_WAIT_MS),
      ]);
      return;
    }
    await playerRef.current?.drain();
  }, [armPlaybackDrain]);

  const onAudioChunk = useCallback(() => {
    audioReceivedRef.current = true;
    setStatus('speaking');
    armPlaybackDrain();
  }, [armPlaybackDrain]);

  const onEvent = useCallback((ev: VoiceEvent) => {
    const turnThread = () => turnThreadRef.current
      ?? useThreadsStore.getState().activeThreadId
      ?? undefined;

    switch (ev.type) {
      case 'hello':
      case 'resumed': {
        setStatus('idle');
        const tid = useThreadsStore.getState().activeThreadId;
        if (tid) {
          setVoiceSessionId(tid, ev.session_id);
        }
        const thread = getActiveThread();
        if (thread && clientRef.current) {
          const profile = useThreadsStore.getState().getActiveClientProfile();
          clientRef.current.setContext({
            conversation_id: thread.conversationId,
            welcome_sent: thread.welcomeSent,
            client_role: thread.clientRole,
            client_profile: clientProfileToWire(profile),
          });
        }
        break;
      }
      case 'transcript_partial':
        setPartial(ev.text);
        setStatus('listening');
        break;
      case 'transcript_final': {
        const text = ev.text.trim();
        setFinalText(text);
        setPartial('');
        const tid = turnThread();
        if (text) appendMessage('user', text, Date.now(), tid);
        setStatus(text ? 'thinking' : 'idle');
        break;
      }
      case 'reply_text':
        setReplyPartial((prev) => {
          const next = prev + ev.text;
          replyRef.current = next;
          return next;
        });
        setStatus('speaking');
        break;
      case 'run_end': {
        applyTrace(ev.event);
        const detail = (ev.event.detail ?? {}) as Record<string, unknown>;
        const tid = turnThread();
        const runResults = extractRunResultsFromRunEnd(detail);
        if (__DEV__ && runResults.length) {
          console.log(
            `[Voice] run_end results=${runResults.length} thread=${tid ?? 'active'}`,
          );
        }
        if (runResults.length) ingestSearchResults(runResults, tid);
        const reply = (
          replyRef.current
          || (typeof detail.reply === 'string' ? detail.reply : '')
        ).trim();
        setReplyPartial('');
        replyRef.current = '';
        if (reply) {
          const now = Date.now();
          const last = lastAssistantAppendRef.current;
          const duplicate = last
            && last.text === reply
            && now - last.at < 5000;
          if (!duplicate) {
            appendMessage('assistant', reply, now, tid);
            lastAssistantAppendRef.current = { text: reply, at: now };
          }
          const threadId = tid ?? useThreadsStore.getState().activeThreadId;
          if (threadId) markWelcomeSent(threadId);
        }
        commitPendingTimelineCards(tid);
        turnThreadRef.current = null;
        setFinalText('');
        void (async () => {
          const failed = ttsFailedRef.current;
          if (!failed) setStatus('speaking');
          turnAwaitingPlaybackRef.current = true;
            try {
            await waitForTurnPlayback();
            const hadPcm = audioReceivedRef.current;
            const played = playerRef.current?.lastPlaySucceeded ?? false;
            if (
              !failed
              && !played
              && reply
              && deviceTtsFallbackEnabled()
              && !hadPcm
            ) {
              if (__DEV__) {
                console.warn('[Voice] no gateway audio — on-device TTS');
              }
              await speakOnDevice(reply);
            } else if (!failed && !played && reply && hadPcm) {
              if (__DEV__) {
                console.warn(
                  '[Voice] gateway audio received but native playback failed — on-device TTS fallback',
                );
              }
              await speakOnDevice(reply);
            }
          } finally {
            turnAwaitingPlaybackRef.current = false;
            clearPlaybackDrainTimer();
            playbackDrainDoneRef.current = null;
            playbackDrainWaitRef.current = null;
            audioReceivedRef.current = false;
            ttsFailedRef.current = false;
            try {
              await prepareRecordingSession();
            } catch { /* noop */ }
            setStatus(failed ? 'error' : 'idle');
          }
        })();
        break;
      }
      case 'tts_error':
        ttsFailedRef.current = true;
        appendMessage('assistant', ev.message, Date.now(), turnThread());
        commitPendingTimelineCards(turnThread());
        turnThreadRef.current = null;
        setStatus('error');
        break;
      case 'cancel':
        // Gateway sends cancel before each new reply (reason=new_turn). Never
        // echo cancel back — that would kill the in-flight orchestrator run and
        // drop listing cards. VoiceClient already flushes playback on cancel.
        void stopDeviceSpeech();
        commitPendingTimelineCards(turnThread());
        if (ev.reason === 'new_turn') {
          break;
        }
        turnThreadRef.current = null;
        setStatus('idle');
        break;
      case 'error':
        commitPendingTimelineCards(turnThread());
        turnThreadRef.current = null;
        if (isRecoverableGatewayError(ev.message)) {
          setStatus('idle');
        } else if (__DEV__) {
          console.warn('[Voice] gateway error (non-fatal):', ev.message);
          setStatus('idle');
        } else {
          setStatus('error');
        }
        break;
      case 'agent_event': {
        applyTrace(ev.event);
        ingestCards(ev.event, turnThread());
        const tool = (ev.event.detail?.tool as string | undefined) ?? '';
        if (ev.event.kind === 'tool_result' && tool === 'search_listings') {
          commitPendingTimelineCards(turnThread());
        }
        break;
      }
      default:
        break;
    }
  }, [applyTrace, clearPlaybackDrainTimer, ingestCards, ingestSearchResults, appendMessage, commitPendingTimelineCards, getActiveThread, markWelcomeSent, setVoiceSessionId, waitForTurnPlayback]);

  const pushThreadContext = useCallback((client: VoiceClient) => {
    const thread = getActiveThread();
    if (!thread) return;
    const profile = useThreadsStore.getState().getActiveClientProfile();
    client.setContext({
      conversation_id: thread.conversationId,
      welcome_sent: thread.welcomeSent,
      client_role: thread.clientRole,
      focus_listing_id: session.focusListingId,
      focus_document_id: session.focusDocumentId,
      focus_person_id: session.focusPersonId,
      focus_person_kind: session.focusPersonKind,
      focus_person_name: session.focusPersonName,
      client_profile: clientProfileToWire(profile),
    });
  }, [
    getActiveThread,
    session.focusDocumentId,
    session.focusListingId,
    session.focusPersonId,
    session.focusPersonKind,
    session.focusPersonName,
  ]);

  const ensureClient = useCallback(async (): Promise<VoiceClient | null> => {
    if (clientRef.current) {
      pushThreadContext(clientRef.current);
      return clientRef.current;
    }
    if (ensureClientPromiseRef.current) {
      return ensureClientPromiseRef.current;
    }

    const tenantId = session.tenantId
      ?? (__DEV__ ? 'stage-cdmx-morelos' : null);
    const userId = session.userId
      ?? (__DEV__ ? 'dev-user' : null);
    const authToken = session.authToken
      ?? (__DEV__ ? 'dev-local' : null);
    if (!tenantId || !userId || !authToken) return null;

    const thread = getActiveThread();
    const player = new AudioPlayer({ inputFormat: resolveTtsInputFormat() });
    playerRef.current = player;

    const client = new VoiceClient({
      url: GATEWAY_URL,
      tenantId,
      userId,
      authToken,
      player,
      initialSessionId: thread?.voiceSessionId ?? null,
      onEvent,
      onStateChange: setClientState,
      onAudioChunk,
    });

    const recorder = new AudioRecorder({
      onFrame: client.sendAudioFrame,
      onLevel: setRmsIn,
    });
    recorderRef.current = recorder;
    (client as unknown as { recorder: AudioRecorder | null }).recorder = recorder;

    ensureClientPromiseRef.current = (async (): Promise<VoiceClient | null> => {
      try {
        await client.connect();
        pushThreadContext(client);
      } catch {
        return null;
      }
      clientRef.current = client;
      return client;
    })();

    try {
      return await ensureClientPromiseRef.current;
    } finally {
      ensureClientPromiseRef.current = null;
    }
  }, [getActiveThread, onAudioChunk, onEvent, pushThreadContext, session.authToken, session.tenantId, session.userId]);

  const onThreadChanged = useCallback(async () => {
    syncFromActiveThread();
    session.setFocusListing(null);
    session.setFocusDocument(null);
    setPartial('');
    setFinalText('');
    setReplyPartial('');
    setStatus('idle');

    const thread = getActiveThread();
    const client = clientRef.current;
    if (client) {
      client.cancel();
      await client.switchSession(thread?.voiceSessionId ?? null);
      pushThreadContext(client);
      return;
    }
    sessionBootstrappedRef.current = false;
    await ensureClient();
  }, [ensureClient, getActiveThread, pushThreadContext, session, syncFromActiveThread]);

  useEffect(() => {
    if (!activeThreadId) return;
    if (prevThreadIdRef.current === null) {
      prevThreadIdRef.current = activeThreadId;
      return;
    }
    if (prevThreadIdRef.current === activeThreadId) return;
    prevThreadIdRef.current = activeThreadId;
    void onThreadChanged();
  }, [activeThreadId, onThreadChanged]);

  useEffect(() => {
    if (!activeThreadId) return;
    if (prevRoleRef.current === null) {
      prevRoleRef.current = activeClientRole;
      return;
    }
    if (prevRoleRef.current === activeClientRole) return;
    prevRoleRef.current = activeClientRole;
    const client = clientRef.current;
    if (client) pushThreadContext(client);
  }, [activeThreadId, activeClientRole, pushThreadContext]);

  const bootstrapSession = useCallback(async () => {
    if (sessionBootstrappedRef.current) return;
    sessionBootstrappedRef.current = true;
    const client = await ensureClient();
    if (!client) sessionBootstrappedRef.current = false;
  }, [ensureClient]);

  const ensureMicPermission = useCallback(async (): Promise<MicPermissionStatus> => {
    const s = await requestMicPermission();
    setMicPermission(s);
    return s;
  }, []);

  const startPTT = useCallback(async () => {
    const perm = await ensureMicPermission();
    if (perm !== 'granted') {
      setStatus('error');
      return;
    }
    const client = await ensureClient();
    if (!client) {
      setStatus('error');
      return;
    }
    setStatus('listening');
    setReplyPartial('');
    audioReceivedRef.current = false;
    ttsFailedRef.current = false;
    await playerRef.current?.flush();
    try {
      await prepareRecordingSession();
      await client.startPTT();
    } catch {
      setStatus('error');
    }
  }, [ensureMicPermission, ensureClient]);

  const endPTT = useCallback(async () => {
    const client = clientRef.current;
    if (!client || client.currentState !== 'recording') {
      return;
    }
    turnThreadRef.current = useThreadsStore.getState().activeThreadId
      ?? useThreadsStore.getState().ensureActiveThread();
    setStatus('thinking');
    await client.endPTT();
  }, []);

  const cancel = useCallback(() => {
    void stopDeviceSpeech();
    clientRef.current?.cancel();
    setStatus('idle');
  }, []);

  const focusListing = useCallback((id: string) => {
    useSession.getState().setFocusListing(id);
    clientRef.current?.focusListing(id);
  }, []);

  const focusDocument = useCallback((id: string) => {
    useSession.getState().setFocusDocument(id);
    clientRef.current?.focusDocument(id);
  }, []);

  const focusPerson = useCallback((
    id: string,
    meta?: { kind?: 'buyer' | 'collaborator' | 'broker'; name?: string },
  ) => {
    useSession.getState().setFocusPerson(id, meta);
    clientRef.current?.focusPerson(id, meta);
  }, []);

  const sendFollowUpMessage = useCallback(async (
    text: string,
    options?: {
      displayText?: string;
      clientProfile?: Record<string, unknown> | null;
      threadId?: string;
    },
  ) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const threadId = options?.threadId
      ?? useThreadsStore.getState().ensureActiveThread();
    turnThreadRef.current = threadId;
    if (useThreadsStore.getState().activeThreadId !== threadId) {
      useThreadsStore.getState().switchThread(threadId);
    }
    const display = (options?.displayText ?? trimmed).trim();
    appendMessage('user', display, Date.now(), threadId);
    setStatus('thinking');
    setReplyPartial('');
    replyRef.current = '';
    const client = await ensureClient();
    if (!client) {
      turnThreadRef.current = null;
      setStatus('error');
      return;
    }
    pushThreadContext(client);
    const wireProfile = options?.clientProfile !== undefined
      ? options.clientProfile
      : clientProfileToWire(
        useThreadsStore.getState().getActiveClientProfile(),
      );
    client.sendUserMessage(trimmed, wireProfile);
  }, [appendMessage, ensureClient, pushThreadContext]);

  const syncThreadContext = useCallback(() => {
    const client = clientRef.current;
    if (client) pushThreadContext(client);
  }, [pushThreadContext]);

  const value = useMemo<VoiceContextValue>(() => ({
    status,
    clientState,
    transcriptPartial: partial,
    transcriptFinal: finalText,
    replyPartial,
    rmsIn,
    rmsOut,
    micPermission,
    startPTT,
    endPTT,
    cancel,
    ensureMicPermission,
    bootstrapSession,
    focusListing,
    focusDocument,
    focusPerson,
    sendFollowUpMessage,
    onThreadChanged,
    syncThreadContext,
  }), [
    status, clientState, partial, finalText, replyPartial, rmsIn, rmsOut, micPermission,
    startPTT, endPTT, cancel, ensureMicPermission, bootstrapSession,
    focusListing, focusDocument, focusPerson, sendFollowUpMessage, onThreadChanged,
    syncThreadContext,
  ]);

  return (
    <VoiceContext.Provider value={value}>
      <View style={styles.root}>{children}</View>
    </VoiceContext.Provider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
