/**
 * Central expo-av audio session switching for iOS mic ↔ speaker handoff.
 */
type ExpoAvModule = typeof import('expo-av');

let _expoAv: ExpoAvModule | null = null;

async function loadExpoAv(): Promise<ExpoAvModule> {
  if (_expoAv) return _expoAv;
  _expoAv = await import('expo-av');
  return _expoAv;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Mic capture — call before every Recording.prepareToRecordAsync on iOS. */
export async function prepareRecordingSession(): Promise<void> {
  const { Audio, InterruptionModeIOS, InterruptionModeAndroid } = await loadExpoAv();
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: true,
    playsInSilentModeIOS: true,
    staysActiveInBackground: false,
    interruptionModeIOS: InterruptionModeIOS.DuckOthers,
    interruptionModeAndroid: InterruptionModeAndroid.DuckOthers,
    shouldDuckAndroid: true,
    playThroughEarpieceAndroid: false,
  });
  // iOS needs a beat after mode switch when coming from TTS playback.
  await sleep(120);
}

/** TTS playback — disables recording so the speaker claims the session. */
export async function preparePlaybackSession(): Promise<void> {
  const { Audio, InterruptionModeIOS, InterruptionModeAndroid } = await loadExpoAv();
  await Audio.setAudioModeAsync({
    allowsRecordingIOS: false,
    playsInSilentModeIOS: true,
    staysActiveInBackground: false,
    interruptionModeIOS: InterruptionModeIOS.DuckOthers,
    interruptionModeAndroid: InterruptionModeAndroid.DuckOthers,
    shouldDuckAndroid: true,
    playThroughEarpieceAndroid: false,
  });
  // Simulator CoreAudio often isn't ready immediately after mic mode.
  await sleep(160);
}
