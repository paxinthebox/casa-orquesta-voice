/**
 * voiceKeywords — Phase 4.2.
 *
 * Lightweight intent matcher for the consent screen + future "yes/no"
 * voice prompts. Normalizes Spanish accents and handles a handful of
 * common variations so the user doesn't have to say one specific word.
 *
 * Examples that match `accept`:
 *   "acepto", "sí acepto", "lo acepto", "estoy de acuerdo",
 *   "claro que sí", "dale", "yes" (en-US fallback)
 *
 * Examples that match `decline`:
 *   "no acepto", "no estoy de acuerdo", "no quiero", "rechazo",
 *   "cancela", "cancela eso", "no" (en-US fallback)
 *
 * The matcher is conservative — if both an accept and a decline keyword
 * match, we return `null` (ambiguous) so the UI doesn't auto-accept on
 * "no acepto pero…". The caller falls back to the explicit button tap.
 */

export type VoiceIntent = 'accept' | 'decline' | null;

const ACCEPT_PHRASES = [
  'acepto',
  'estoy de acuerdo',
  'de acuerdo',
  'claro que si',
  'claro',
  'dale',
  'va',
  'okay',
  'ok',
  'si acepto',
  'lo acepto',
  // en-US fallback
  'yes',
  'i agree',
];

const DECLINE_PHRASES = [
  'no acepto',
  'no estoy de acuerdo',
  'no quiero',
  'rechazo',
  'cancela',
  'cancelar',
  // en-US fallback
  'no',
  'cancel',
  'decline',
];

const DECLINE_PREFIX_OVERRIDES = ['no ', 'nunca ', 'jamas '];

/**
 * Classify a transcript into a consent intent. Returns:
 *   'accept'  — user said something that means "yes"
 *   'decline' — user said something that means "no"
 *   null      — no clear match or ambiguous input
 */
export function classifyConsentIntent(transcript: string): VoiceIntent {
  const t = _normalize(transcript);
  if (!t) return null;

  // First: explicit decline override. "no acepto" must NOT match
  // ACCEPT_PHRASES — handle by checking decline before accept.
  const declineHit = _firstMatch(t, DECLINE_PHRASES);

  // Ambiguity guard: "acepto pero no..." or "no acepto" — if any decline
  // prefix appears before the accept word, we count it as a decline.
  const acceptHit = _firstMatchExcludingNegated(t);

  if (declineHit && acceptHit) {
    // If decline appears before accept, treat as decline.
    if (t.indexOf(declineHit) < t.indexOf(acceptHit)) return 'decline';
    return null; // truly ambiguous
  }
  if (declineHit) return 'decline';
  if (acceptHit) return 'accept';
  return null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function _normalize(s: string): string {
  return s
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')   // strip diacritics
    .replace(/[¡¿!?,.;:]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _firstMatch(s: string, list: string[]): string | null {
  for (const p of list) {
    if (s.includes(p)) return p;
  }
  return null;
}

/**
 * Find an accept phrase that ISN'T negated by a preceding "no".
 * "no acepto" → no accept match (decline path handles it).
 * "claro que sí, acepto" → accept match on "acepto".
 */
function _firstMatchExcludingNegated(s: string): string | null {
  for (const p of ACCEPT_PHRASES) {
    const idx = s.indexOf(p);
    if (idx < 0) continue;
    // Check the 8 chars before the match for a negation prefix.
    const lookback = s.slice(Math.max(0, idx - 8), idx);
    const negated = DECLINE_PREFIX_OVERRIDES.some((n) => lookback.endsWith(n));
    if (!negated) return p;
  }
  return null;
}
