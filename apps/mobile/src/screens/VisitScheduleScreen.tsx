/**
 * VisitScheduleScreen — propose three slots and confirm a visit (pilot flow).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  SafeAreaView,
  ActivityIndicator,
  ScrollView,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { useSession } from '@/state/SessionProvider';
import {
  confirmVisit,
  proposeVisit,
  type ProposeVisitResponse,
  type VisitSlot,
} from '@/api/schedulingClient';

type Props = NativeStackScreenProps<RootStackParamList, 'VisitSchedule'>;

function formatSlotEs(slot: VisitSlot): string {
  const d = new Date(slot.start);
  if (Number.isNaN(d.getTime())) return slot.start;
  return d.toLocaleString('es-MX', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function VisitScheduleScreen({ navigation, route }: Props) {
  const { t } = useTranslation();
  const userId = useSession((s) => s.userId);
  const { listingId, listingTitle } = route.params;

  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<ProposeVisitResponse | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  const buyerId = userId ?? (__DEV__ ? 'dev-user' : 'demo-buyer');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const resp = await proposeVisit(listingId, buyerId);
    if (!resp?.visit_id || !resp.slots?.length) {
      setError(t('visit.error_propose'));
      setProposal(null);
    } else {
      setProposal(resp);
    }
    setLoading(false);
  }, [buyerId, listingId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const onConfirm = async (index: number) => {
    if (!proposal) return;
    setConfirming(true);
    setError(null);
    const result = await confirmVisit(proposal.visit_id, index);
    setConfirming(false);
    if (!result?.selected_slot) {
      setError(t('visit.error_confirm'));
      return;
    }
    setConfirmed(true);
  };

  return (
    <SafeAreaView style={styles.root} testID="screen-visit-schedule">
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.title}>{t('visit.title')}</Text>
        {listingTitle ? (
          <Text style={styles.subtitle} numberOfLines={2}>{listingTitle}</Text>
        ) : null}
        <Text style={styles.hint}>{t('visit.subtitle')}</Text>

        {loading ? (
          <ActivityIndicator color={colors.agentLocator} style={{ marginTop: spacing.xl }} />
        ) : null}

        {error ? <Text style={styles.error}>{error}</Text> : null}

        {confirmed ? (
          <View style={styles.successBox}>
            <Text style={styles.successTitle}>{t('visit.confirmed_title')}</Text>
            <Text style={styles.successBody}>{t('visit.confirmed_body')}</Text>
          </View>
        ) : null}

        {!loading && proposal && !confirmed
          ? proposal.slots.map((slot, index) => (
              <Pressable
                key={`${slot.start}-${index}`}
                style={styles.slotRow}
                disabled={confirming}
                onPress={() => { void onConfirm(index); }}
                testID={`visit-slot-${index}`}
              >
                <Text style={styles.slotWhen}>{formatSlotEs(slot)}</Text>
                <Text style={styles.slotMeta}>{t('visit.slot_parties')}</Text>
              </Pressable>
            ))
          : null}

        <Pressable
          style={styles.backBtn}
          onPress={() => navigation.goBack()}
          accessibilityRole="button"
        >
          <Text style={styles.backLabel}>{t('common.back')}</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  scroll: { padding: spacing.xl },
  title: { ...typography.h2, color: colors.textPrimary },
  subtitle: { ...typography.body, color: colors.textSecondary, marginTop: spacing.xs },
  hint: { ...typography.caption, color: colors.textMuted, marginTop: spacing.m },
  error: { ...typography.body, color: colors.danger, marginTop: spacing.m },
  slotRow: {
    marginTop: spacing.m,
    padding: spacing.l,
    borderRadius: radii.m,
    backgroundColor: colors.navyEl2,
    borderWidth: 1,
    borderColor: colors.hairline,
  },
  slotWhen: { ...typography.body, color: colors.textPrimary, textTransform: 'capitalize' },
  slotMeta: { ...typography.caption, color: colors.textMuted, marginTop: spacing.xs },
  successBox: {
    marginTop: spacing.l,
    padding: spacing.l,
    borderRadius: radii.m,
    backgroundColor: colors.goldFaint,
    borderWidth: 1,
    borderColor: colors.gold,
  },
  successTitle: { ...typography.body, color: colors.gold, fontWeight: '600' },
  successBody: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.xs },
  backBtn: { marginTop: spacing.xl, alignItems: 'center', padding: spacing.m },
  backLabel: { ...typography.body, color: colors.textSecondary },
});
