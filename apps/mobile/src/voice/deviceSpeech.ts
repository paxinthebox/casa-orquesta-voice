/**
 * On-device TTS fallback — used when the gateway sends reply text but
 * ElevenLabs returns no PCM (e.g. free-tier API blocks library voices).
 */
export async function speakOnDevice(text: string): Promise<void> {
  const trimmed = sanitizeForSpeech(text.trim());
  if (!trimmed) return;
  try {
    const Speech = await import('expo-speech');
    await new Promise<void>((resolve) => {
      Speech.speak(trimmed, {
        language: 'es-MX',
        rate: 0.95,
        onDone: () => resolve(),
        onStopped: () => resolve(),
        onError: () => resolve(),
      });
    });
  } catch {
    // expo-speech unavailable in tests — ignore.
  }
}

export async function stopDeviceSpeech(): Promise<void> {
  try {
    const Speech = await import('expo-speech');
    Speech.stop();
  } catch {
    /* noop */
  }
}

/** Expand abbreviations so iOS/Android TTS reads es-MX naturally. */
export function sanitizeForSpeech(text: string): string {
  return text
    .replace(/\$\s*([\d]+(?:[.,]\d+)?)\s*(?:MDP|M\b|millones?)?/gi, (_, n: string) => {
      const val = parseFloat(n.replace(',', '.'));
      if (Number.isNaN(val)) return _;
      if (val === Math.floor(val)) return `${Math.floor(val)} millones de pesos`;
      const whole = Math.floor(val);
      const frac = Math.round((val - whole) * 10);
      return frac ? `${whole} punto ${frac} millones de pesos` : `${whole} millones de pesos`;
    })
    .replace(/(\d+(?:[.,]\d+)?)\s*m²/gi, '$1 metros cuadrados')
    .replace(/\bCDMX\b/g, 'Ciudad de México');
}
