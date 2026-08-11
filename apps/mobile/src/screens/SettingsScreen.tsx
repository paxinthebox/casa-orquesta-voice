/**
 * SettingsScreen — Phase 3.1 stub.
 *
 * Renders sections for Account, Voice, Privacy, About. Most rows are
 * inert in this phase; P4 wires logout, delete-account, data-export.
 */
import React from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  SafeAreaView,
  ScrollView,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { useSession } from '@/state/SessionProvider';
import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { setLocale } from '@/locale/i18n';

type Props = NativeStackScreenProps<RootStackParamList, 'Settings'>;

const APP_VERSION = '0.1.0';
const APP_BUILD = '1';

export function SettingsScreen(_: Props) {
  const { t } = useTranslation();
  const locale = useSession((s) => s.locale);
  const setLocaleStore = useSession((s) => s.setLocale);
  const reset = useSession((s) => s.reset);

  const switchLocale = async () => {
    const next = locale === 'es-MX' ? 'en-US' : 'es-MX';
    setLocaleStore(next);
    await setLocale(next);
  };

  return (
    <SafeAreaView style={styles.root} testID="screen-settings">
      <ScrollView contentContainerStyle={styles.scroll}>
        <Section title={t('settings.section_account')}>
          <Row label={t('settings.logout')} testID="settings-logout" onPress={reset} />
          <Row label={t('settings.delete_account')} testID="settings-delete" danger />
        </Section>

        <Section title={t('settings.section_voice')}>
          <Row label={t('settings.voice_tts')} value={t('settings.voice_tts_native')} />
          <Row label={t('settings.tts_provider')} value="ElevenLabs Flash" />
        </Section>

        <Section title={t('settings.section_privacy')}>
          <Row label={t('settings.privacy_aviso')} />
          <Row label={t('settings.data_export')} />
        </Section>

        <Section title={t('settings.section_about')}>
          <Row
            label={t('settings.language')}
            value={locale}
            onPress={switchLocale}
            testID="settings-language"
          />
          <Row
            label={t('settings.version', { version: APP_VERSION })}
            value={t('settings.build', { build: APP_BUILD })}
          />
        </Section>
      </ScrollView>
    </SafeAreaView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.card}>{children}</View>
    </View>
  );
}

function Row({
  label,
  value,
  onPress,
  testID,
  danger,
}: {
  label: string;
  value?: string;
  onPress?: () => void;
  testID?: string;
  danger?: boolean;
}) {
  const Inner = (
    <View style={styles.row}>
      <Text style={[styles.rowLabel, danger && { color: colors.danger }]}>{label}</Text>
      {value ? <Text style={styles.rowValue}>{value}</Text> : null}
    </View>
  );
  if (!onPress) return Inner;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      testID={testID}
      android_ripple={{ color: colors.navyEl2 }}
    >
      {Inner}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  scroll: { padding: spacing.l, gap: spacing.l },
  section: { gap: spacing.s },
  sectionTitle: {
    ...typography.caption,
    color: colors.textMuted,
    textTransform: 'uppercase' as const,
    letterSpacing: 1.2,
    paddingHorizontal: spacing.s,
  },
  card: {
    backgroundColor: colors.navyEl1,
    borderRadius: radii.l,
    borderWidth: 1, borderColor: colors.hairline,
  },
  row: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: spacing.l, paddingHorizontal: spacing.l,
    borderBottomWidth: 1, borderBottomColor: colors.hairline,
  },
  rowLabel: { ...typography.body, color: colors.textPrimary },
  rowValue: { ...typography.body, color: colors.textMuted },
});
