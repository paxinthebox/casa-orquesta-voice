/**
 * ThreadPicker — legacy chip entry; home uses ThreadsSection instead.
 */
import React, { useState } from 'react';
import { Pressable, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { useThreadsStore } from '@/state/threadsStore';
import { ThreadManageModal } from '@/components/ThreadManageModal';

export function ThreadPicker() {
  const { t } = useTranslation();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const threads = useThreadsStore((s) => s.threads);
  const activeThreadId = useThreadsStore((s) => s.activeThreadId);
  const [open, setOpen] = useState(false);

  const active = threads.find((th) => th.id === activeThreadId) ?? threads[0];
  const roleLabel = (role: 'buyer' | 'seller') =>
    role === 'seller' ? t('threads.role_seller') : t('threads.role_buyer');

  const chipSubtitle = active
    ? `${active.label} · ${roleLabel(active.clientRole)}`
    : t('threads.default_label');

  return (
    <>
      <Pressable
        style={styles.chip}
        onPress={() => setOpen(true)}
        accessibilityRole="button"
        testID="thread-picker-open"
      >
        <Text style={styles.chipLabel} numberOfLines={1}>
          {chipSubtitle}
        </Text>
        <Text style={styles.chipChevron}>▾</Text>
      </Pressable>

      <ThreadManageModal
        visible={open}
        onClose={() => setOpen(false)}
        navigation={navigation}
      />
    </>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    maxWidth: 260,
    paddingHorizontal: spacing.m,
    paddingVertical: spacing.s,
    borderRadius: radii.pill,
    backgroundColor: colors.navyEl2,
    borderWidth: 1,
    borderColor: colors.hairline,
    marginBottom: spacing.s,
  },
  chipLabel: { ...typography.caption, color: colors.textPrimary, flexShrink: 1 },
  chipChevron: { color: colors.textSecondary, marginLeft: spacing.xs, fontSize: 12 },
});
