/**
 * ConsentModal — Phase 4.2.
 *
 * Full Aviso de Privacidad screen. Users can:
 *   1. Read the entire LFPDPPP-compliant text (scrollable).
 *   2. Accept via tap → "Acepto y continuar"
 *   3. Accept via voice → say "acepto" while the modal is open.
 *   4. Decline via tap → "Aún no"
 *   5. Decline via voice → say "no acepto" / "cancela"
 *
 * Acceptance writes a consent record to the identity service tagged with
 * the SHA-256 hash of the exact text the user saw. That hash is what
 * the audit log carries, so we can later prove which version of the
 * Aviso was in effect at the moment of acceptance.
 *
 * The component is presentational and self-contained — `<ConsentGate>`
 * decides whether to mount it.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  View,
  Text,
  Pressable,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';

import { colors, spacing, radii, typography } from '@/theme';
import { useSession } from '@/state/SessionProvider';
import { classifyConsentIntent } from '@/voice/voiceKeywords';
import {
  AVISO_VERSION, getAvisoText,
} from './aviso';
import {
  recordConsent, sha256Hex, IdentityApiError,
} from '@/api/identityClient';

export type ConsentModalResult = 'accepted' | 'declined';

export interface ConsentModalProps {
  /** Called after a successful accept *and* the server write completes. */
  onAccepted: (result: { id: string; textSha256: string }) => void;
  /** Called when user taps "Aún no" or says "no acepto". */
  onDeclined: () => void;
  /** Override the identityClient.recordConsent for tests. */
  recordConsentFn?: typeof recordConsent;
  /** Optional voice transcripts when VoiceProvider is mounted. */
  transcriptPartial?: string;
  transcriptFinal?: string;
}

type ServerState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'error'; message: string };

export function ConsentModal({
  onAccepted,
  onDeclined,
  recordConsentFn = recordConsent,
  transcriptPartial = '',
  transcriptFinal = '',
}: ConsentModalProps) {
  const { t } = useTranslation();
  const locale = useSession((s) => s.locale);
  const insets = useSafeAreaInsets();

  const avisoText = useMemo(() => getAvisoText(locale), [locale]);
  const [textSha256, setTextSha256] = useState<string | null>(null);
  const [server, setServer] = useState<ServerState>({ kind: 'idle' });
  const [scrolledToEnd, setScrolledToEnd] = useState(false);

  // Hash the text once, lazily. The hash is what ties the consent
  // record to "the exact text the user saw".
  useEffect(() => {
    let cancelled = false;
    void sha256Hex(avisoText).then((h) => {
      if (!cancelled) setTextSha256(h);
    });
    return () => { cancelled = true; };
  }, [avisoText]);

  // Voice intent: only fire on a *final* transcript so we don't auto-
  // accept on a partial "ace..." that turns into "acepto pero no...".
  useEffect(() => {
    const intent = classifyConsentIntent(transcriptFinal);
    if (intent === 'accept') void handleAccept('voice');
    else if (intent === 'decline') void handleDecline('voice');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcriptFinal]);

  const canAccept = textSha256 !== null && server.kind !== 'submitting' && scrolledToEnd;

  async function handleAccept(channel: 'ui' | 'voice') {
    if (!textSha256 || server.kind === 'submitting') return;
    setServer({ kind: 'submitting' });
    try {
      const cr = await recordConsentFn({
        purpose: 'lfpdppp',
        granted: true,
        textVersion: AVISO_VERSION,
        textSha256,
        channel,
      });
      onAccepted({ id: cr.id, textSha256 });
    } catch (e) {
      const msg = e instanceof IdentityApiError
        ? `HTTP ${e.status}`
        : (e as Error)?.message ?? String(e);
      setServer({ kind: 'error', message: msg });
    }
  }

  async function handleDecline(_channel: 'ui' | 'voice') {
    onDeclined();
  }

  return (
    <View
      style={[
        styles.root,
        { paddingTop: insets.top, paddingBottom: insets.bottom },
      ]}
      testID="consent-modal"
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t('consent.title')}</Text>
        <Text style={styles.subtitle}>{t('consent.subtitle')}</Text>
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollInner}
        onScroll={(e) => {
          const { layoutMeasurement, contentOffset, contentSize } = e.nativeEvent;
          const atEnd =
            layoutMeasurement.height + contentOffset.y >= contentSize.height - 24;
          if (atEnd && !scrolledToEnd) setScrolledToEnd(true);
        }}
        scrollEventThrottle={48}
        testID="consent-text"
      >
        <Text style={styles.avisoText}>{avisoText}</Text>
      </ScrollView>

      {!scrolledToEnd && (
        <Text style={styles.scrollHint}>{t('consent.scroll_to_continue')}</Text>
      )}

      {transcriptPartial ? (
        <Text style={styles.voiceHint} testID="consent-voice-hint">
          🎤 {transcriptPartial}
        </Text>
      ) : null}

      {server.kind === 'error' ? (
        <Text style={styles.errorText}>{server.message}</Text>
      ) : null}

      <View style={styles.actions}>
        <Pressable
          onPress={() => void handleDecline('ui')}
          accessibilityRole="button"
          style={[styles.cta, styles.ctaSecondary]}
          testID="consent-decline"
        >
          <Text style={styles.ctaLabelSecondary}>{t('consent.cta_decline')}</Text>
        </Pressable>

        <Pressable
          onPress={() => void handleAccept('ui')}
          accessibilityRole="button"
          accessibilityState={{ disabled: !canAccept }}
          disabled={!canAccept}
          style={[
            styles.cta,
            styles.ctaPrimary,
            !canAccept && styles.ctaDisabled,
          ]}
          testID="consent-accept"
        >
          {server.kind === 'submitting' ? (
            <ActivityIndicator color={colors.ink} />
          ) : (
            <Text style={styles.ctaLabelPrimary}>{t('consent.cta_accept')}</Text>
          )}
        </Pressable>
      </View>

      <Text style={styles.footer}>{t('consent.footer')}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  header: { padding: spacing.xl, gap: spacing.s },
  title: { ...typography.h1, color: colors.textPrimary },
  subtitle: { ...typography.body, color: colors.textSecondary },
  scroll: {
    flex: 1,
    marginHorizontal: spacing.l,
    marginVertical: spacing.s,
    borderRadius: radii.l,
    backgroundColor: colors.navyEl1,
    borderWidth: 1, borderColor: colors.hairline,
  },
  scrollInner: { padding: spacing.l },
  avisoText: { ...typography.body, color: colors.textPrimary, lineHeight: 22 },
  scrollHint: {
    ...typography.caption, color: colors.textMuted,
    textAlign: 'center', paddingVertical: spacing.s,
  },
  voiceHint: {
    ...typography.caption, color: colors.gold,
    textAlign: 'center', paddingVertical: spacing.s,
  },
  errorText: {
    ...typography.caption, color: colors.danger,
    textAlign: 'center', paddingVertical: spacing.s,
  },
  actions: {
    flexDirection: 'row', gap: spacing.s,
    padding: spacing.l,
  },
  cta: {
    flex: 1, paddingVertical: spacing.l,
    borderRadius: radii.l,
    alignItems: 'center', justifyContent: 'center',
  },
  ctaPrimary: { backgroundColor: colors.gold },
  ctaSecondary: { backgroundColor: 'transparent', borderWidth: 1, borderColor: colors.hairline },
  ctaDisabled: { opacity: 0.45 },
  ctaLabelPrimary: { ...typography.bodyBold, color: colors.ink },
  ctaLabelSecondary: { ...typography.body, color: colors.textPrimary },
  footer: {
    ...typography.caption, color: colors.textMuted,
    textAlign: 'center', padding: spacing.l,
  },
});
