/**
 * identityClient — Phase 4.2.
 *
 * Thin HTTP wrapper around the identity service (`services/identity`).
 * Exposes:
 *
 *     recordConsent(opts)        → POST /consent
 *     revokeConsent(purpose)     → POST /consent/revoke
 *     listConsents()             → GET  /consent
 *     requestDsarExport()        → POST /dsar/export  → Blob
 *     requestDsarDelete()        → POST /dsar/delete  → receipt
 *
 * Implementation notes:
 *   - The auth token is read from `useSession.getState().authToken`
 *     at call-time, so a fresh token after re-login is picked up
 *     without remounting any components.
 *   - The base URL is `process.env.EXPO_PUBLIC_ORCHESTRATOR_URL` because
 *     the gateway service is the single ingress in production (the
 *     identity service is behind it at /identity/*). Override via
 *     `EXPO_PUBLIC_IDENTITY_URL` if you point at identity directly.
 *   - `fetch` is the runtime — RN ships a Hermes-compatible implementation;
 *     React Native polyfills `Blob` so the export download Just Works.
 *   - On non-2xx responses we throw an `IdentityApiError` carrying the
 *     HTTP status and the parsed detail so the UI can show the localized
 *     error string from `t('errors…')`.
 */
import { useSession } from '@/state/SessionProvider';

const IDENTITY_BASE_URL =
  (process.env.EXPO_PUBLIC_IDENTITY_URL as string | undefined)
  ?? (process.env.EXPO_PUBLIC_ORCHESTRATOR_URL as string | undefined)
  ?? 'http://localhost:8002';

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------
export class IdentityApiError extends Error {
  public readonly status: number;
  public readonly detail: unknown;
  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `identity api error ${status}`);
    this.name = 'IdentityApiError';
    this.status = status;
    this.detail = detail;
  }
}

// ---------------------------------------------------------------------------
// Request types
// ---------------------------------------------------------------------------
export interface RecordConsentRequest {
  /** "lfpdppp" | "mic" | "transcripts" | "marketing" | … */
  purpose: string;
  granted: boolean;
  /** Human-readable Aviso version, e.g. "aviso-v1". */
  textVersion: string;
  /** SHA-256 hex digest of the exact text the user accepted. */
  textSha256: string;
  /** "ui" | "voice" | "settings" | "api" — defaults to "ui". */
  channel?: 'ui' | 'voice' | 'settings' | 'api';
}

export interface ConsentRecord {
  id: string;
  purpose: string;
  granted: boolean;
  created_at: number;
  revoked?: boolean;
  revoked_at?: number | null;
}

export interface DsarDeleteResponse {
  user_id: string;
  tenant_id: string;
  services: Array<{
    service: string;
    deleted: boolean;
    status: number;
    count: number;
    error: string | null;
  }>;
  sessions_revoked: number;
  consents_revoked: number;
  note: string;
}

// ---------------------------------------------------------------------------
// Internal fetch helper
// ---------------------------------------------------------------------------
async function _fetch(
  path: string,
  init: RequestInit,
): Promise<Response> {
  const session = useSession.getState();
  const headers = new Headers(init.headers);
  headers.set('Accept', headers.get('Accept') ?? 'application/json');
  if (session.authToken) {
    headers.set('Authorization', `Bearer ${session.authToken}`);
  }
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type',
      headers.get('Content-Type') ?? 'application/json');
  }
  const url = `${IDENTITY_BASE_URL.replace(/\/$/, '')}${path}`;
  const resp = await fetch(url, { ...init, headers });
  if (!resp.ok) {
    let detail: unknown = null;
    try { detail = await resp.json(); } catch { /* no body */ }
    throw new IdentityApiError(resp.status, detail);
  }
  return resp;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
export async function recordConsent(
  req: RecordConsentRequest,
): Promise<ConsentRecord> {
  const resp = await _fetch('/consent', {
    method: 'POST',
    body: JSON.stringify({
      purpose: req.purpose,
      granted: req.granted,
      text_version: req.textVersion,
      text_sha256: req.textSha256,
      channel: req.channel ?? 'ui',
    }),
  });
  return (await resp.json()) as ConsentRecord;
}

export async function revokeConsent(purpose: string): Promise<{ revoked: number }> {
  const resp = await _fetch('/consent/revoke', {
    method: 'POST',
    body: JSON.stringify({ purpose }),
  });
  return (await resp.json()) as { revoked: number };
}

export async function listConsents(): Promise<ConsentRecord[]> {
  const resp = await _fetch('/consent', { method: 'GET' });
  const body = (await resp.json()) as { consents: ConsentRecord[] };
  return body.consents;
}

/**
 * Triggers the DSAR export and returns the ZIP as a `Blob`. The mobile
 * UI hands the blob to the device's "Save to Files" picker so the user
 * actually retains the export — we don't persist it server-side.
 */
export async function requestDsarExport(): Promise<Blob> {
  const resp = await _fetch('/dsar/export', { method: 'POST' });
  return await resp.blob();
}

export async function requestDsarDelete(): Promise<DsarDeleteResponse> {
  const resp = await _fetch('/dsar/delete', { method: 'POST' });
  return (await resp.json()) as DsarDeleteResponse;
}

// ---------------------------------------------------------------------------
// Phase 4.4 — invite codes + phone OTP onboarding
// ---------------------------------------------------------------------------
export interface InviteValidateResult {
  ok: boolean;
  reason: string;
  tenant_id?: string | null;
  label?: string | null;
  role?: string | null;
}

export async function validateInvite(code: string): Promise<InviteValidateResult> {
  const resp = await _fetch('/auth/invite/validate', {
    method: 'POST',
    body: JSON.stringify({ code }),
  });
  return (await resp.json()) as InviteValidateResult;
}

export interface AuthStartResult {
  challenge_id: string;
  expires_in: number;
  tenant_id: string;
  invite_label?: string | null;
  dev_code?: string | null;
}

export async function startOtp(opts: {
  phoneE164: string;
  inviteCode?: string;
  locale?: 'es-MX' | 'en-US';
}): Promise<AuthStartResult> {
  const resp = await _fetch('/auth/start', {
    method: 'POST',
    body: JSON.stringify({
      phone_e164: opts.phoneE164,
      locale: opts.locale ?? 'es-MX',
      invite_code: opts.inviteCode,
    }),
  });
  return (await resp.json()) as AuthStartResult;
}

export interface AuthVerifyResult {
  access_token: string;
  expires_in: number;
  user: { id: string; tenant_id: string; phone_e164: string; role: string };
  tenant: { id: string; name: string; country: string };
}

export async function verifyOtp(opts: {
  phoneE164: string;
  challengeId: string;
  code: string;
  inviteCode?: string;
}): Promise<AuthVerifyResult> {
  const resp = await _fetch('/auth/verify', {
    method: 'POST',
    body: JSON.stringify({
      phone_e164: opts.phoneE164,
      challenge_id: opts.challengeId,
      code: opts.code,
      invite_code: opts.inviteCode,
    }),
  });
  return (await resp.json()) as AuthVerifyResult;
}

// ---------------------------------------------------------------------------
// SHA-256 helper (used by ConsentModal to hash the Aviso text)
// ---------------------------------------------------------------------------
/**
 * Compute SHA-256 of a UTF-8 string and return the hex digest.
 * Uses Web Crypto if available (RN's `expo-crypto` provides it on RN 0.76+),
 * otherwise falls back to a tiny pure-JS implementation that's fine for
 * the ~5 kB Aviso de Privacidad text we hash here.
 */
export async function sha256Hex(text: string): Promise<string> {
  // 1. Try Web Crypto / expo-crypto's subtle.
  try {
    const subtle = (globalThis as { crypto?: { subtle?: SubtleCrypto } })
      .crypto?.subtle;
    if (subtle) {
      const buf = new TextEncoder().encode(text);
      const digest = await subtle.digest('SHA-256', buf);
      return Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
    }
  } catch { /* fall through */ }
  // 2. Pure-JS fallback.
  return _sha256Fallback(text);
}

// ---- Pure-JS SHA-256 (RFC 6234) — only used when WebCrypto absent.
function _sha256Fallback(text: string): string {
  const bytes = new TextEncoder().encode(text);
  // Pre-processing: pad message
  const l = bytes.length;
  const bitLen = l * 8;
  const withOne = new Uint8Array(((l + 9 + 63) >> 6) << 6);
  withOne.set(bytes);
  withOne[l] = 0x80;
  // append 64-bit big-endian length
  const dv = new DataView(withOne.buffer);
  dv.setUint32(withOne.length - 4, bitLen >>> 0, false);
  dv.setUint32(withOne.length - 8, Math.floor(bitLen / 0x100000000), false);

  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  let h = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);

  const w = new Uint32Array(64);
  for (let i = 0; i < withOne.length; i += 64) {
    for (let t = 0; t < 16; t++) w[t] = dv.getUint32(i + t * 4, false);
    for (let t = 16; t < 64; t++) {
      const s0 = _rotr(w[t - 15]!, 7) ^ _rotr(w[t - 15]!, 18) ^ (w[t - 15]! >>> 3);
      const s1 = _rotr(w[t - 2]!, 17) ^ _rotr(w[t - 2]!, 19) ^ (w[t - 2]! >>> 10);
      w[t] = (w[t - 16]! + s0 + w[t - 7]! + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let t = 0; t < 64; t++) {
      const S1 = _rotr(e!, 6) ^ _rotr(e!, 11) ^ _rotr(e!, 25);
      const ch = (e! & f!) ^ ((~e! & 0xffffffff) & g!);
      const temp1 = (hh! + S1 + ch + K[t]! + w[t]!) >>> 0;
      const S0 = _rotr(a!, 2) ^ _rotr(a!, 13) ^ _rotr(a!, 22);
      const maj = (a! & b!) ^ (a! & c!) ^ (b! & c!);
      const temp2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e;
      e = (d! + temp1) >>> 0;
      d = c; c = b; b = a;
      a = (temp1 + temp2) >>> 0;
    }
    h[0] = (h[0]! + a!) >>> 0;
    h[1] = (h[1]! + b!) >>> 0;
    h[2] = (h[2]! + c!) >>> 0;
    h[3] = (h[3]! + d!) >>> 0;
    h[4] = (h[4]! + e!) >>> 0;
    h[5] = (h[5]! + f!) >>> 0;
    h[6] = (h[6]! + g!) >>> 0;
    h[7] = (h[7]! + hh!) >>> 0;
  }
  return Array.from(h)
    .map((x) => x.toString(16).padStart(8, '0'))
    .join('');
}

function _rotr(x: number, n: number): number {
  return ((x >>> n) | (x << (32 - n))) >>> 0;
}
