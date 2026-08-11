/**
 * AudioPlayer — Phase 3.2.
 *
 * Buffers TTS audio for a turn, then plays **one** file via expo-av.
 * Stage gateway sends raw PCM (ElevenLabs pcm_16000) — we wrap to WAV.
 * MP3 is supported when `inputFormat` is `mp3` or auto-detected via ID3.
 */

import { preparePlaybackSession, prepareRecordingSession } from './audioSession';

export { preparePlaybackSession, prepareRecordingSession } from './audioSession';

export type TtsInputFormat = 'pcm' | 'mp3' | 'auto';

export interface PlayerOptions {
  sampleRate?: number;
  /** How to interpret gateway binary frames. Default pcm (matches voice-gateway). */
  inputFormat?: TtsInputFormat;
  backend?: AudioPlayerBackend;
  onLevel?: (rmsNormalized: number) => void;
}

/** Resolve build-time TTS wire format from EXPO_PUBLIC_TTS_AUDIO_FORMAT. */
export function resolveTtsInputFormat(): TtsInputFormat {
  const raw = (process.env.EXPO_PUBLIC_TTS_AUDIO_FORMAT ?? '').toLowerCase();
  if (raw.includes('mp3')) return 'mp3';
  if (raw.includes('pcm')) return 'pcm';
  // MP3 from ElevenLabs plays reliably on iOS; PCM/WAV often fails on simulator.
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Platform } = require('react-native') as typeof import('react-native');
    if (Platform.OS === 'ios' || Platform.OS === 'android') return 'mp3';
  } catch {
    /* tests / non-RN */
  }
  return 'mp3';
}

export interface AudioPlayerBackend {
  playBuffer(data: Uint8Array, ext: 'mp3' | 'wav'): Promise<boolean>;
  stop(): Promise<void>;
  release(): Promise<void>;
}

export class AudioPlayer {
  private readonly opts: Required<Omit<PlayerOptions, 'backend' | 'onLevel'>>
    & Pick<PlayerOptions, 'backend' | 'onLevel'>;
  private readonly backend: AudioPlayerBackend;

  private pending: Uint8Array = new Uint8Array(0);
  private queue: Promise<void> = Promise.resolve();
  private epoch = 0;
  private _playing = false;
  /** True if any MP3/WAV segment in this utterance played successfully. */
  private _utterancePlayOk = false;

  constructor(opts: PlayerOptions = {}) {
    this.opts = {
      sampleRate: opts.sampleRate ?? 16000,
      inputFormat: opts.inputFormat ?? resolveTtsInputFormat(),
      onLevel: opts.onLevel,
      backend: opts.backend,
    };
    this.backend = opts.backend ?? createDefaultPlayerBackend();
  }

  get isPlaying(): boolean { return this._playing; }
  /** Any segment succeeded — avoids false fallback when a trailing chunk fails. */
  get lastPlaySucceeded(): boolean { return this._utterancePlayOk; }

  async feed(frame: Uint8Array | ArrayBuffer): Promise<void> {
    const u8 = frame instanceof ArrayBuffer ? new Uint8Array(frame) : frame;
    if (u8.byteLength === 0) return;
    const merged = new Uint8Array(this.pending.byteLength + u8.byteLength);
    merged.set(this.pending, 0);
    merged.set(u8, this.pending.byteLength);
    this.pending = merged;
  }

  async flushTail(): Promise<void> {
    if (this.pending.byteLength === 0) return;
    const utterance = this.pending;
    this.pending = new Uint8Array(0);
    this._enqueue(utterance);
  }

  async drain(): Promise<void> {
    await this.flushTail();
    await this.queue;
    try { await prepareRecordingSession(); }
    catch { /* restore mic for next PTT */ }
  }

  async flush(): Promise<void> {
    this.epoch += 1;
    this.pending = new Uint8Array(0);
    this._playing = false;
    this._utterancePlayOk = false;
    try { await this.backend.stop(); }
    catch { /* noop */ }
    try { await prepareRecordingSession(); }
    catch { /* noop */ }
  }

  async close(): Promise<void> {
    await this.flush();
    await this.backend.release();
  }

  private _enqueue(raw: Uint8Array): void {
    const startedEpoch = this.epoch;
    const fmt = this.opts.inputFormat ?? 'pcm';
    if (fmt === 'mp3' || (fmt === 'auto' && isLikelyMp3(raw))) {
      // REST ElevenLabs delivers one MP3 stream in many TCP chunks — play as a
      // single file. Splitting on embedded ID3 tags yields invalid fragments
      // (MediaToolbox err -12864 on iOS).
      if (raw.byteLength >= 128) {
        this._enqueueOne(raw, 'mp3', startedEpoch);
      }
      return;
    }
    const aligned = alignPcm16(raw);
    if (aligned.byteLength < 640) return;
    this._enqueueOne(pcm16ToWav(aligned, this.opts.sampleRate), 'wav', startedEpoch);
  }

  private _enqueueOne(data: Uint8Array, ext: 'mp3' | 'wav', startedEpoch: number): void {
    if (data.byteLength < 128) return;
    this.queue = this.queue.then(async () => {
      if (this.epoch !== startedEpoch) return;
      this._playing = true;
      try {
        const ok = await this.backend.playBuffer(data, ext);
        if (ok && this.epoch === startedEpoch) this._utterancePlayOk = true;
      } catch {
        /* keep _utterancePlayOk if an earlier segment already played */
      } finally {
        if (this.epoch === startedEpoch) this._playing = false;
      }
    });
  }
}

export function createDefaultPlayerBackend(): AudioPlayerBackend {
  return new ExpoAvPlayerBackend();
}

type ExpoAvModule = typeof import('expo-av');
type ExpoFsModule = typeof import('expo-file-system');
type SoundObject = Awaited<
  ReturnType<ExpoAvModule['Audio']['Sound']['createAsync']>
>['sound'];

class ExpoAvPlayerBackend implements AudioPlayerBackend {
  private sound: SoundObject | null = null;
  private fileCounter = 0;

  async playBuffer(data: Uint8Array, ext: 'mp3' | 'wav'): Promise<boolean> {
    // Simulator often fails the first play after mic→speaker switch; retry once.
    for (let attempt = 1; attempt <= 2; attempt++) {
      const ok = await this._playOnce(data, ext, attempt);
      if (ok) return true;
      if (attempt === 1) {
        if (__DEV__) console.warn('[AudioPlayer] play attempt 1 failed — retrying');
        await sleep(200);
      }
    }
    return false;
  }

  private async _playOnce(
    data: Uint8Array,
    ext: 'mp3' | 'wav',
    attempt: number,
  ): Promise<boolean> {
    const { Audio } = await loadExpoAvSound();
    await preparePlaybackSession();
    // Extra settle after recording→playback; iOS Simulator audio device is flaky.
    await sleep(attempt === 1 ? 120 : 220);
    await this._unloadSound();

    const uri = await writeTempAudio(data, this.fileCounter++, ext);
    try {
      const { sound, status } = await Audio.Sound.createAsync(
        { uri },
        { shouldPlay: false, progressUpdateIntervalMillis: 100 },
        undefined,
        true,
      );
      if (!status.isLoaded) {
        if (__DEV__) console.warn('[AudioPlayer] load failed:', uri, ext);
        await sound.unloadAsync().catch(() => {});
        return false;
      }
      this.sound = sound;
      const playedOk = waitForFinish(sound);
      const playStatus = await sound.playAsync();
      if (!playStatus.isLoaded || playStatus.error) {
        if (__DEV__) {
          console.warn('[AudioPlayer] playAsync failed:', playStatus);
        }
        return false;
      }
      const ok = await playedOk;
      if (__DEV__ && ok) {
        console.log('[AudioPlayer] played', ext, data.byteLength, 'bytes');
      }
      return ok;
    } catch (e) {
      if (__DEV__) console.warn('[AudioPlayer] play failed:', e);
      return false;
    } finally {
      await this._unloadSound();
    }
  }

  private async _unloadSound(): Promise<void> {
    if (!this.sound) return;
    const s = this.sound;
    this.sound = null;
    // Prefer unload-only — stopAsync on an already-finished FigFilePlayer
    // spam MediaToolbox -12864 / -12785 on the iOS Simulator.
    try {
      const status = await s.getStatusAsync();
      if (status.isLoaded && status.isPlaying) {
        await s.stopAsync().catch(() => {});
      }
    } catch { /* noop */ }
    try { await s.unloadAsync(); } catch { /* noop */ }
  }

  async stop(): Promise<void> { await this._unloadSound(); }
  async release(): Promise<void> { await this._unloadSound(); }
}

export class MockPlayerBackend implements AudioPlayerBackend {
  public readonly played: Array<{ data: Uint8Array; ext: string }> = [];
  public stops = 0;
  public released = false;

  async playBuffer(data: Uint8Array, ext: 'mp3' | 'wav'): Promise<boolean> {
    this.played.push({ data, ext });
    return true;
  }
  async stop(): Promise<void> { this.stops += 1; }
  async release(): Promise<void> { this.released = true; }
}

let _expoAvSound: ExpoAvModule | null = null;
async function loadExpoAvSound(): Promise<ExpoAvModule> {
  if (_expoAvSound) return _expoAvSound;
  _expoAvSound = await import('expo-av');
  return _expoAvSound;
}

let _expoFs: ExpoFsModule | null = null;
async function loadFs(): Promise<ExpoFsModule> {
  if (_expoFs) return _expoFs;
  _expoFs = await import('expo-file-system');
  return _expoFs;
}

async function writeTempAudio(data: Uint8Array, n: number, ext: string): Promise<string> {
  const FileSystem = await loadFs();
  const dir = `${FileSystem.cacheDirectory}co-tts/`;
  try { await FileSystem.makeDirectoryAsync(dir, { intermediates: true }); }
  catch { /* exists */ }
  const path = `${dir}utterance-${n}.${ext}`;
  await FileSystem.writeAsStringAsync(path, uint8ToBase64(data), {
    encoding: FileSystem.EncodingType.Base64,
  });
  return path;
}

/** ID3 tag or a valid MPEG layer-III frame sync — avoids false positives on raw PCM. */
function isLikelyMp3(b: Uint8Array): boolean {
  if (b.length >= 3 && b[0] === 0x49 && b[1] === 0x44 && b[2] === 0x33) return true;
  if (b.length >= 2 && b[0] === 0xff && (b[1]! & 0xe0) === 0xe0) {
    // Layer III only — random PCM rarely matches both sync + layer bits.
    return (b[1]! & 0x06) === 0x02;
  }
  return false;
}

/** ElevenLabs may emit multiple ID3-prefixed MP3 segments per utterance. */
function splitMp3Segments(buf: Uint8Array): Uint8Array[] {
  const starts: number[] = [];
  for (let i = 0; i + 2 < buf.length; i++) {
    if (buf[i] === 0x49 && buf[i + 1] === 0x44 && buf[i + 2] === 0x33) {
      starts.push(i);
    }
  }
  if (starts.length <= 1) return [buf];
  const out: Uint8Array[] = [];
  for (let s = 0; s < starts.length; s++) {
    const start = starts[s]!;
    const end = s + 1 < starts.length ? starts[s + 1]! : buf.length;
    const seg = buf.subarray(start, end);
    if (seg.byteLength >= 128) out.push(seg);
  }
  return out.length ? out : [buf];
}

async function waitForFinish(sound: SoundObject): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    let done = false;
    let sawPlayback = false;
    let maxPositionMs = 0;
    const finish = (success: boolean) => {
      if (done) return;
      done = true;
      resolve(success);
    };
    sound.setOnPlaybackStatusUpdate?.((status) => {
      if (done) return;
      if (!status.isLoaded) {
        // Unload after audible progress still counts as success (simulator teardown noise).
        if (sawPlayback && maxPositionMs > 80) {
          finish(true);
          return;
        }
        if ('error' in status && status.error) finish(false);
        return;
      }
      if (status.isPlaying || (status.positionMillis ?? 0) > 0) {
        sawPlayback = true;
        maxPositionMs = Math.max(maxPositionMs, status.positionMillis ?? 0);
      }
      if (status.didJustFinish) {
        finish(maxPositionMs > 50 || (status.positionMillis ?? 0) > 50);
      }
    });
    void (async () => {
      // Bridgeless iOS sometimes skips didJustFinish — poll as backup.
      for (let i = 0; i < 800 && !done; i++) {
        await sleep(100);
        try {
          const status = await sound.getStatusAsync();
          if (!status.isLoaded) {
            if (sawPlayback && maxPositionMs > 80) {
              finish(true);
              return;
            }
            continue;
          }
          if (status.isPlaying || (status.positionMillis ?? 0) > 0) {
            sawPlayback = true;
            maxPositionMs = Math.max(maxPositionMs, status.positionMillis ?? 0);
          }
          if (status.didJustFinish) {
            finish(maxPositionMs > 50);
            return;
          }
          if (
            !status.isPlaying
            && maxPositionMs > 200
            && (status.durationMillis ?? 0) > 0
            && maxPositionMs >= (status.durationMillis ?? 0) - 150
          ) {
            finish(true);
            return;
          }
        } catch {
          if (sawPlayback && maxPositionMs > 80) {
            finish(true);
            return;
          }
        }
      }
      finish(sawPlayback && maxPositionMs > 80);
    })();
    setTimeout(() => finish(sawPlayback && maxPositionMs > 80), 120_000);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function pcm16ToWav(pcm: Uint8Array, sampleRate: number): Uint8Array {
  const aligned = alignPcm16(pcm);
  const dataLen = aligned.byteLength;
  const blockAlign = 2;
  const byteRate = sampleRate * blockAlign;
  const buf = new Uint8Array(44 + dataLen);
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);

  writeStr(dv, 0, 'RIFF');
  dv.setUint32(4, 36 + dataLen, true);
  writeStr(dv, 8, 'WAVE');
  writeStr(dv, 12, 'fmt ');
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, byteRate, true);
  dv.setUint16(32, blockAlign, true);
  dv.setUint16(34, 16, true);
  writeStr(dv, 36, 'data');
  dv.setUint32(40, dataLen, true);
  buf.set(aligned, 44);
  return buf;
}

export function alignPcm16(pcm: Uint8Array): Uint8Array {
  if (pcm.byteLength < 2) return new Uint8Array(0);
  if (pcm.byteLength % 2 === 0) return pcm;
  return pcm.subarray(0, pcm.byteLength - 1);
}

function writeStr(dv: DataView, off: number, s: string): void {
  for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i));
}

function uint8ToBase64(u8: Uint8Array): string {
  if (typeof globalThis.btoa === 'function') {
    let bin = '';
    for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]!);
    return globalThis.btoa(bin);
  }
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let out = '';
  let i = 0;
  for (; i + 2 < u8.length; i += 3) {
    const n = (u8[i]! << 16) | (u8[i + 1]! << 8) | u8[i + 2]!;
    out += chars[(n >> 18) & 63]! + chars[(n >> 12) & 63]!
         + chars[(n >> 6) & 63]!  + chars[n & 63]!;
  }
  if (i < u8.length) {
    let n = u8[i]! << 16;
    if (i + 1 < u8.length) n |= u8[i + 1]! << 8;
    out += chars[(n >> 18) & 63]! + chars[(n >> 12) & 63]!;
    out += i + 1 < u8.length ? chars[(n >> 6) & 63]! : '=';
    out += '=';
  }
  return out;
}
