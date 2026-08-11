/**
 * ConsentGate — Phase 4.2.
 *
 * Blocks app entry until LFPDPPP consent is granted. Mounts
 * `<ConsentModal>` and writes the consent record to the identity
 * service. Local Zustand state (`consentGiven`) is flipped only after
 * the server write succeeds — that way a flaky network can never leave
 * us with a "locally accepted but server doesn't know" mismatch.
 *
 * Pre-auth gap (Phase 4 TODO): consent runs before login so there is no
 * bearer token yet. In dev/stage we fall back to a local stub record so
 * the pilot can proceed. In production the identity service will expose
 * an unauthenticated POST /consent/anonymous endpoint keyed by device_id
 * that gets linked to the account on first login (Phase 4.5).
 */
import React, { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';

import { useSession } from '@/state/SessionProvider';
import { ConsentModal } from './ConsentModal';
import { recordConsent, type RecordConsentRequest, type ConsentRecord } from '@/api/identityClient';
import { colors } from '@/theme';

/**
 * Tries the real identity service. Falls back to a local stub when the
 * server is unreachable or returns 401/404 (pre-login, dev environment).
 * The stub is clearly flagged so it can be re-synced after login in Phase 4.5.
 */
async function recordConsentWithFallback(
  opts: RecordConsentRequest,
): Promise<ConsentRecord> {
  try {
    return await recordConsent(opts);
  } catch (e: unknown) {
    const isNetworkOrAuth =
      e instanceof TypeError || // fetch network error
      (e as { status?: number })?.status === 401 ||
      (e as { status?: number })?.status === 404;

    if (__DEV__ && isNetworkOrAuth) {
      console.warn(
        '[ConsentGate] Identity service unreachable or not authenticated. ' +
        'Accepting locally for dev/stage pilot. Re-sync post-login (Phase 4.5).',
        e,
      );
      return {
        id: `local-pre-auth-${Date.now()}`,
        purpose: opts.purpose,
        granted: opts.granted,
        created_at: Math.floor(Date.now() / 1000),
      };
    }
    throw e;
  }
}

export function ConsentGate({ children }: { children: React.ReactNode }) {
  const consentGiven = useSession((s) => s.consentGiven);
  const setConsentGiven = useSession((s) => s.setConsentGiven);

  // Local dev: skip consent so onboarding is reachable without scrolling the aviso.
  useEffect(() => {
    if (__DEV__ && process.env.EXPO_PUBLIC_SKIP_CONSENT !== '0' && !consentGiven) {
      setConsentGiven(true);
      if (__DEV__) console.log('[ConsentGate] dev skip → onboarding');
    }
  }, [consentGiven, setConsentGiven]);

  if (consentGiven) {
    return <View style={styles.root}>{children}</View>;
  }

  return (
    <View style={styles.root}>
      <ConsentModal
      onAccepted={() => setConsentGiven(true)}
      onDeclined={() => {
        // We don't flip the gate; the modal stays mounted. P4.5 ops
        // adds an exit affordance + telemetry for "declined-then-left".
      }}
      recordConsentFn={recordConsentWithFallback}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
});
