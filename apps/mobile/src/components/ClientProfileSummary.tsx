/**
 * Per-thread client profile summary — criteria at a glance + edit / search actions.
 */
import React, { useMemo } from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import {
  selectActiveClientProfileRaw,
  selectActiveThread,
  useThreadsStore,
} from '@/state/threadsStore';
import { useVoice } from '@/voice/VoiceProvider';
import {
  buildClientSearchPrompt,
  buildClientSearchDisplayLabel,
  clientProfileToWire,
  emptyClientProfile,
  isClientProfileFilled,
  normalizeClientProfile,
  summarizeClientProfile,
} from '@/utils/clientProfile';

type Nav = NativeStackNavigationProp<RootStackParamList, 'Chat'>;

export interface ClientProfileSummaryProps {
  navigation: Nav;
}

export function ClientProfileSummary({ navigation }: ClientProfileSummaryProps) {
  const { t } = useTranslation();
  const { sendFollowUpMessage, syncThreadContext, status } = useVoice();
  const activeThread = useThreadsStore(selectActiveThread);
  const rawProfile = useThreadsStore(selectActiveClientProfileRaw);
  const profile = useMemo(
    () => (rawProfile ? normalizeClientProfile(rawProfile) : emptyClientProfile()),
    [rawProfile],
  );

  const busy = status === 'thinking' || status === 'speaking';
  const filled = useMemo(() => isClientProfileFilled(profile), [profile]);
  const chips = useMemo(() => summarizeClientProfile(profile), [profile]);

  const openForm = () => {
    navigation.navigate('ClientProfile');
  };

  const searchWithProfile = () => {
    if (!filled || busy) return;
    syncThreadContext();
    void sendFollowUpMessage(buildClientSearchPrompt(profile), {
      displayText: buildClientSearchDisplayLabel(profile),
      clientProfile: clientProfileToWire(profile),
    });
  };

  return (
    <View style={styles.wrap} testID="client-profile-summary">
      <View style={styles.header}>
        <Text style={styles.title}>{t('clientProfile.thread_title')}</Text>
        {activeThread ? (
          <Text style={styles.threadName} numberOfLines={1}>
            {activeThread.label}
          </Text>
        ) : null}
      </View>

      {filled ? (
        <View style={styles.chipRow}>
          {chips.map((chip, index) => (
            <View key={`${chip}-${index}`} style={styles.chip}>
              <Text style={styles.chipLabel} numberOfLines={1}>{chip}</Text>
            </View>
          ))}
        </View>
      ) : (
        <Text style={styles.emptyHint}>{t('clientProfile.thread_empty')}</Text>
      )}

      <View style={styles.actions}>
        <Pressable
          style={styles.editBtn}
          onPress={openForm}
          accessibilityRole="button"
          testID="client-profile-summary-edit"
        >
          <Text style={styles.editLabel}>
            {filled ? t('clientProfile.thread_edit') : t('clientProfile.thread_create')}
          </Text>
        </Pressable>
        {filled ? (
          <Pressable
            style={[styles.searchBtn, busy && styles.btnDisabled]}
            onPress={searchWithProfile}
            disabled={busy}
            accessibilityRole="button"
            testID="client-profile-summary-search"
          >
            {busy ? (
              <ActivityIndicator color={colors.navy} size="small" />
            ) : (
              <Text style={styles.searchLabel}>{t('clientProfile.thread_search')}</Text>
            )}
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    marginTop: spacing.m,
    padding: spacing.m,
    borderRadius: radii.m,
    backgroundColor: colors.navyEl1,
    borderWidth: 1,
    borderColor: colors.hairline,
    gap: spacing.s,
  },
  header: { gap: 2 },
  title: { ...typography.caption, color: colors.textMuted, textTransform: 'uppercase' },
  threadName: { ...typography.bodyBold, color: colors.textPrimary },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  chip: {
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.s,
    borderRadius: radii.pill,
    backgroundColor: colors.navyEl2,
    borderWidth: 1,
    borderColor: colors.hairline,
    maxWidth: '100%',
  },
  chipLabel: { ...typography.caption, color: colors.textSecondary },
  emptyHint: { ...typography.caption, color: colors.textMuted },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.s, marginTop: spacing.xs },
  editBtn: {
    paddingVertical: spacing.s,
    paddingHorizontal: spacing.m,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.agentLocator,
  },
  editLabel: { ...typography.caption, color: colors.agentLocator, fontWeight: '600' },
  searchBtn: {
    paddingVertical: spacing.s,
    paddingHorizontal: spacing.m,
    borderRadius: radii.pill,
    backgroundColor: colors.agentLocator,
    minWidth: 120,
    alignItems: 'center',
  },
  searchLabel: { ...typography.caption, color: colors.navy, fontWeight: '600' },
  btnDisabled: { opacity: 0.5 },
});
