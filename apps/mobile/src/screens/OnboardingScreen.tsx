/**
 * OnboardingScreen — Phase 4.4.
 *
 * Five-step wizard:
 *   1. splash    — three-feature hero + locale toggle (was P3.1).
 *   2. invite    — type a closed-beta invite code (XXXX-XXXX),
 *                  validated against /auth/invite/validate.
 *   3. phone     — E.164 phone entry; POST /auth/start with the
 *                  invite_code + phone returns a challenge_id.
 *   4. otp       — 6-digit code from the SMS; POST /auth/verify
 *                  exchanges it for an internal JWT and binds the
 *                  invite to the new user.
 *   5. welcome   — friendly "Bienvenido a la beta" success state;
 *                  CTA replaces to Home.
 *
 * The wizard owns its own state machine to keep the navigation graph
 * shallow — every step is just a render branch of this component.
 * That makes it easy to step back via the local `back()` helper without
 * touching the React Navigation stack.
 */
import React, { useMemo, useState } from 'react';
import {
  View,
  Text,
  Pressable,
  TextInput,
  StyleSheet,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useTranslation } from 'react-i18next';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { useSession } from '@/state/SessionProvider';
import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { setLocale } from '@/locale/i18n';
import {
  validateInvite,
  startOtp,
  verifyOtp,
  IdentityApiError,
  type AuthStartResult,
  type InviteValidateResult,
} from '@/api/identityClient';

type Props = NativeStackScreenProps<RootStackParamList, 'Onboarding'>;

export type OnboardingStep = 'splash' | 'invite' | 'phone' | 'otp' | 'welcome';

export interface OnboardingScreenProps {
  /** Test hook — start at a specific step. */
  initialStep?: OnboardingStep;
}

export function OnboardingScreen({
  navigation,
}: Props & OnboardingScreenProps) {
  const { t } = useTranslation();
  const setOnboardingComplete = useSession((s) => s.setOnboardingComplete);
  const locale = useSession((s) => s.locale);
  const setLocaleStore = useSession((s) => s.setLocale);
  const setIdentity = useSession((s) => s.setIdentity);

  const [step, setStep] = useState<OnboardingStep>('splash');
  const [invite, setInvite] = useState('');
  const [inviteResult, setInviteResult] = useState<InviteValidateResult | null>(null);
  const [phone, setPhone] = useState('+52');
  const [otp, setOtp] = useState('');
  const [challenge, setChallenge] = useState<AuthStartResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---- Helpers ----
  const back = () => {
    setError(null);
    if (step === 'invite') setStep('splash');
    else if (step === 'phone') setStep('invite');
    else if (step === 'otp') setStep('phone');
  };

  const finishToHome = () => {
    setOnboardingComplete(true);
    navigation.replace('Threads');
  };

  const switchLocale = async (l: 'es-MX' | 'en-US') => {
    setLocaleStore(l);
    await setLocale(l);
  };

  // ---- Step 2: invite ----
  async function submitInvite() {
    setError(null);
    setBusy(true);
    try {
      const v = await validateInvite(invite);
      setInviteResult(v);
      if (v.ok) setStep('phone');
      else setError(t(`onboarding.invite_error.${v.reason}`,
                     { defaultValue: t('onboarding.invite_error.unknown_code') }));
    } catch {
      setError(t('errors.network'));
    } finally {
      setBusy(false);
    }
  }

  // ---- Step 3: phone ----
  async function submitPhone() {
    setError(null);
    setBusy(true);
    try {
      const r = await startOtp({
        phoneE164: phone,
        inviteCode: invite,
        locale,
      });
      setChallenge(r);
      setStep('otp');
    } catch (e) {
      const msg = e instanceof IdentityApiError
        ? `HTTP ${e.status}: ${JSON.stringify(e.detail)}`
        : t('errors.network');
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  // ---- Step 4: otp ----
  async function submitOtp() {
    if (!challenge) return;
    setError(null);
    setBusy(true);
    try {
      const v = await verifyOtp({
        phoneE164: phone,
        challengeId: challenge.challenge_id,
        code: otp,
        inviteCode: invite,
      });
      // Bind the freshly-issued token + identity into the session.
      setIdentity({
        tenantId: v.user.tenant_id,
        userId: v.user.id,
        authToken: v.access_token,
      });
      setStep('welcome');
    } catch (e) {
      const msg = e instanceof IdentityApiError
        ? t(`onboarding.otp_error.${e.status}`,
            { defaultValue: `HTTP ${e.status}` })
        : t('errors.network');
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  // ---- Render ----
  return (
    <SafeAreaView style={styles.root} edges={['top', 'bottom']} testID="screen-onboarding">
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {step === 'splash' && (
          <SplashStep
            locale={locale}
            onSwitchLocale={switchLocale}
            onContinue={() => setStep('invite')}
          />
        )}
        {step === 'invite' && (
          <InviteStep
            invite={invite}
            setInvite={setInvite}
            inviteResult={inviteResult}
            busy={busy}
            error={error}
            onBack={back}
            onSubmit={submitInvite}
          />
        )}
        {step === 'phone' && (
          <PhoneStep
            phone={phone}
            setPhone={setPhone}
            inviteLabel={inviteResult?.label ?? null}
            busy={busy}
            error={error}
            onBack={back}
            onSubmit={submitPhone}
          />
        )}
        {step === 'otp' && (
          <OtpStep
            otp={otp}
            setOtp={setOtp}
            phone={phone}
            devCode={challenge?.dev_code ?? null}
            busy={busy}
            error={error}
            onBack={back}
            onSubmit={submitOtp}
          />
        )}
        {step === 'welcome' && (
          <WelcomeStep tenantLabel={inviteResult?.label ?? null}
                       onContinue={finishToHome} />
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// ----------------------------------------------------------------------------
// Step components
// ----------------------------------------------------------------------------
function SplashStep({
  locale, onSwitchLocale, onContinue,
}: {
  locale: 'es-MX' | 'en-US';
  onSwitchLocale: (l: 'es-MX' | 'en-US') => Promise<void>;
  onContinue: () => void;
}) {
  const { t } = useTranslation();
  const features = [
    t('onboarding.feature_search'),
    t('onboarding.feature_audit'),
    t('onboarding.feature_schedule'),
  ];

  return (
    <ScrollView contentContainerStyle={styles.scroll}>
      <View style={styles.hero}>
        <Text style={styles.brand}>{t('app.name')}</Text>
        <Text style={styles.title}>{t('onboarding.title')}</Text>
        <Text style={styles.subtitle}>{t('onboarding.subtitle')}</Text>
      </View>

      <View style={styles.featureList}>
        {features.map((f, i) => (
          <View style={styles.featureRow} key={i}>
            <View style={styles.bullet}><Text style={styles.bulletText}>{i + 1}</Text></View>
            <Text style={styles.featureText}>{f}</Text>
          </View>
        ))}
      </View>

      <View style={styles.langSection}>
        <Text style={styles.langLabel}>{t('onboarding.language_label')}</Text>
        <View style={styles.langRow}>
          <LangChip active={locale === 'es-MX'}
                    label={t('onboarding.language_es')}
                    onPress={() => onSwitchLocale('es-MX')}
                    testID="lang-es" />
          <LangChip active={locale === 'en-US'}
                    label={t('onboarding.language_en')}
                    onPress={() => onSwitchLocale('en-US')}
                    testID="lang-en" />
        </View>
      </View>

      <Pressable
        accessibilityRole="button"
        onPress={onContinue}
        style={styles.cta}
        testID="onboarding-continue"
      >
        <Text style={styles.ctaLabel}>{t('onboarding.cta_continue')}</Text>
      </Pressable>
    </ScrollView>
  );
}

function InviteStep({
  invite, setInvite, inviteResult, busy, error, onBack, onSubmit,
}: {
  invite: string;
  setInvite: (v: string) => void;
  inviteResult: InviteValidateResult | null;
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  const formatted = useMemo(() => formatInviteInput(invite), [invite]);
  return (
    <ScrollView contentContainerStyle={styles.scroll}>
      <Text style={styles.stepLabel}>{t('onboarding.step_invite')}</Text>
      <Text style={styles.title}>{t('onboarding.invite_title')}</Text>
      <Text style={styles.subtitle}>{t('onboarding.invite_subtitle')}</Text>

      <TextInput
        value={formatted}
        onChangeText={(s) => setInvite(s.toUpperCase())}
        autoCapitalize="characters"
        autoCorrect={false}
        autoComplete="off"
        maxLength={9}
        placeholder="XXXX-XXXX"
        placeholderTextColor={colors.textMuted}
        style={styles.input}
        testID="invite-input"
      />

      {inviteResult?.ok ? (
        <Text style={styles.successHint}>
          ✓ {inviteResult.label} · {inviteResult.role}
        </Text>
      ) : null}
      {__DEV__ && process.env.EXPO_PUBLIC_DEV_INVITE_CODE ? (
        <Text style={styles.devHint}>
          dev code: {process.env.EXPO_PUBLIC_DEV_INVITE_CODE}
        </Text>
      ) : null}
      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <View style={styles.stepActions}>
        <Pressable onPress={onBack} style={[styles.cta, styles.ctaSecondary]}>
          <Text style={styles.ctaLabelSecondary}>{t('common.back')}</Text>
        </Pressable>
        <Pressable
          onPress={onSubmit}
          disabled={busy || formatted.length < 9}
          style={[
            styles.cta, styles.ctaPrimary,
            (busy || formatted.length < 9) && styles.ctaDisabled,
          ]}
          testID="invite-submit"
        >
          {busy
            ? <ActivityIndicator color={colors.ink} />
            : <Text style={styles.ctaLabel}>{t('common.next')}</Text>}
        </Pressable>
      </View>
    </ScrollView>
  );
}

function PhoneStep({
  phone, setPhone, inviteLabel, busy, error, onBack, onSubmit,
}: {
  phone: string;
  setPhone: (v: string) => void;
  inviteLabel: string | null;
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  const valid = /^\+\d{8,15}$/.test(phone);
  return (
    <ScrollView contentContainerStyle={styles.scroll}>
      <Text style={styles.stepLabel}>{t('onboarding.step_phone')}</Text>
      {inviteLabel
        ? <Text style={styles.inviteLabel}>★ {inviteLabel}</Text>
        : null}
      <Text style={styles.title}>{t('onboarding.phone_title')}</Text>
      <Text style={styles.subtitle}>{t('onboarding.phone_subtitle')}</Text>

      <TextInput
        value={phone}
        onChangeText={setPhone}
        keyboardType="phone-pad"
        autoCorrect={false}
        autoComplete="tel"
        textContentType="telephoneNumber"
        placeholder="+52 55…"
        placeholderTextColor={colors.textMuted}
        style={styles.input}
        testID="phone-input"
      />
      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <View style={styles.stepActions}>
        <Pressable onPress={onBack} style={[styles.cta, styles.ctaSecondary]}>
          <Text style={styles.ctaLabelSecondary}>{t('common.back')}</Text>
        </Pressable>
        <Pressable
          onPress={onSubmit}
          disabled={busy || !valid}
          style={[styles.cta, styles.ctaPrimary,
                  (busy || !valid) && styles.ctaDisabled]}
          testID="phone-submit"
        >
          {busy
            ? <ActivityIndicator color={colors.ink} />
            : <Text style={styles.ctaLabel}>{t('onboarding.phone_send_otp')}</Text>}
        </Pressable>
      </View>
    </ScrollView>
  );
}

function OtpStep({
  otp, setOtp, phone, devCode, busy, error, onBack, onSubmit,
}: {
  otp: string;
  setOtp: (v: string) => void;
  phone: string;
  devCode: string | null;
  busy: boolean;
  error: string | null;
  onBack: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  const valid = /^\d{6}$/.test(otp);
  return (
    <ScrollView contentContainerStyle={styles.scroll}>
      <Text style={styles.stepLabel}>{t('onboarding.step_otp')}</Text>
      <Text style={styles.title}>{t('onboarding.otp_title')}</Text>
      <Text style={styles.subtitle}>
        {t('onboarding.otp_subtitle', { phone })}
      </Text>

      <TextInput
        value={otp}
        onChangeText={(s) => setOtp(s.replace(/\D/g, '').slice(0, 6))}
        keyboardType="number-pad"
        autoComplete="sms-otp"
        textContentType="oneTimeCode"
        maxLength={6}
        placeholder="000000"
        placeholderTextColor={colors.textMuted}
        style={[styles.input, styles.inputOtp]}
        testID="otp-input"
      />

      {devCode ? (
        <Text style={styles.devHint}>dev code: {devCode}</Text>
      ) : null}
      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <View style={styles.stepActions}>
        <Pressable onPress={onBack} style={[styles.cta, styles.ctaSecondary]}>
          <Text style={styles.ctaLabelSecondary}>{t('common.back')}</Text>
        </Pressable>
        <Pressable
          onPress={onSubmit}
          disabled={busy || !valid}
          style={[styles.cta, styles.ctaPrimary,
                  (busy || !valid) && styles.ctaDisabled]}
          testID="otp-submit"
        >
          {busy
            ? <ActivityIndicator color={colors.ink} />
            : <Text style={styles.ctaLabel}>{t('onboarding.otp_verify')}</Text>}
        </Pressable>
      </View>
    </ScrollView>
  );
}

function WelcomeStep({
  tenantLabel, onContinue,
}: {
  tenantLabel: string | null;
  onContinue: () => void;
}) {
  const { t } = useTranslation();
  return (
    <ScrollView contentContainerStyle={[styles.scroll, { justifyContent: 'center' }]}>
      <Text style={styles.heroEmoji}>🎉</Text>
      <Text style={styles.title}>{t('onboarding.welcome_title')}</Text>
      <Text style={styles.subtitle}>{t('onboarding.welcome_subtitle')}</Text>
      {tenantLabel
        ? <Text style={styles.inviteLabel}>{tenantLabel}</Text>
        : null}
      <View style={styles.welcomeList}>
        <WelcomeRow text={t('onboarding.welcome_bullet_1')} />
        <WelcomeRow text={t('onboarding.welcome_bullet_2')} />
        <WelcomeRow text={t('onboarding.welcome_bullet_3')} />
      </View>
      <Pressable onPress={onContinue} style={styles.cta} testID="welcome-continue">
        <Text style={styles.ctaLabel}>{t('onboarding.welcome_cta')}</Text>
      </Pressable>
    </ScrollView>
  );
}

function WelcomeRow({ text }: { text: string }) {
  return (
    <View style={styles.welcomeRow}>
      <Text style={styles.welcomeBullet}>✓</Text>
      <Text style={styles.welcomeText}>{text}</Text>
    </View>
  );
}

function LangChip({
  label, active, onPress, testID,
}: { label: string; active: boolean; onPress: () => void; testID?: string }) {
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={[styles.chip, active && styles.chipActive]}
      testID={testID}
    >
      <Text style={[styles.chipLabel, active && styles.chipLabelActive]}>{label}</Text>
    </Pressable>
  );
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------
/**
 * Format a raw invite-code string as the user types: uppercase, strip
 * everything that isn't an alphanumeric char, then re-insert the dash
 * after the 4th character. So "abcd-1234" / "ABCD1234" / "abcd 1234"
 * all render as "ABCD-1234".
 *
 * Exported via the parent module for the test mirror.
 */
export function formatInviteInput(raw: string): string {
  const s = (raw || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (s.length <= 4) return s;
  return `${s.slice(0, 4)}-${s.slice(4, 8)}`;
}

// ----------------------------------------------------------------------------
// Styles
// ----------------------------------------------------------------------------
const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  scroll: {
    padding: spacing.xl, gap: spacing.l,
    paddingTop: 48, paddingBottom: 64,
  },
  hero: { gap: spacing.s, marginTop: spacing.xl },
  brand: { ...typography.caption, color: colors.gold, letterSpacing: 2 },
  title: { ...typography.display, color: colors.textPrimary },
  subtitle: { ...typography.body, color: colors.textSecondary },
  stepLabel: {
    ...typography.caption, color: colors.gold,
    textTransform: 'uppercase' as const, letterSpacing: 2,
  },
  inviteLabel: {
    ...typography.bodyBold, color: colors.gold,
    marginTop: spacing.xs,
  },
  featureList: { gap: spacing.m, marginTop: spacing.l },
  featureRow: { flexDirection: 'row', gap: spacing.m, alignItems: 'flex-start' },
  bullet: {
    width: 28, height: 28, borderRadius: radii.pill,
    backgroundColor: colors.goldFaint,
    alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: colors.gold,
  },
  bulletText: { ...typography.bodyBold, color: colors.gold },
  featureText: { flex: 1, ...typography.body, color: colors.textPrimary },
  langSection: { gap: spacing.s, marginTop: spacing.l },
  langLabel: { ...typography.caption, color: colors.textMuted },
  langRow: { flexDirection: 'row', gap: spacing.s, flexWrap: 'wrap' },
  chip: {
    paddingHorizontal: spacing.l, paddingVertical: spacing.s,
    borderRadius: radii.pill,
    borderWidth: 1, borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
  },
  chipActive: {
    borderColor: colors.gold, backgroundColor: colors.goldFaint,
  },
  chipLabel: { ...typography.body, color: colors.textPrimary },
  chipLabelActive: { color: colors.gold, fontWeight: '600' as const },
  input: {
    marginTop: spacing.l,
    backgroundColor: colors.navyEl1,
    borderWidth: 1, borderColor: colors.hairline,
    borderRadius: radii.l,
    padding: spacing.l,
    color: colors.textPrimary,
    ...typography.h3,
    fontVariant: ['tabular-nums' as const],
  },
  inputOtp: {
    textAlign: 'center', letterSpacing: 8, fontSize: 26,
  },
  successHint: {
    ...typography.body, color: colors.success,
    marginTop: spacing.s,
  },
  errorText: {
    ...typography.body, color: colors.danger,
    marginTop: spacing.s,
  },
  devHint: {
    ...typography.caption, color: colors.textMuted,
    marginTop: spacing.s, textAlign: 'center',
  },
  stepActions: {
    flexDirection: 'row', gap: spacing.s, marginTop: spacing.xl,
  },
  cta: {
    flex: 1,
    backgroundColor: colors.gold,
    paddingVertical: spacing.l,
    borderRadius: radii.l,
    alignItems: 'center', justifyContent: 'center',
  },
  ctaPrimary: { backgroundColor: colors.gold },
  ctaSecondary: {
    backgroundColor: 'transparent',
    borderWidth: 1, borderColor: colors.hairline,
  },
  ctaDisabled: { opacity: 0.45 },
  ctaLabel: { ...typography.bodyBold, color: colors.ink },
  ctaLabelSecondary: { ...typography.body, color: colors.textPrimary },
  heroEmoji: { fontSize: 64, textAlign: 'center', marginBottom: spacing.l },
  welcomeList: {
    gap: spacing.s, marginVertical: spacing.l,
    padding: spacing.l, borderRadius: radii.l,
    borderWidth: 1, borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
  },
  welcomeRow: { flexDirection: 'row', gap: spacing.s, alignItems: 'flex-start' },
  welcomeBullet: { ...typography.bodyBold, color: colors.gold },
  welcomeText: { flex: 1, ...typography.body, color: colors.textPrimary },
});
