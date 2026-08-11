/**
 * SlotCard — Phase 3.3.
 *
 * Visual companion to a proposed visit slot from `realestate_agent`.
 * Tapping it pins the listing context (if the slot has one) and routes
 * to the detail screen where the user can confirm/decline.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

import { CardBase } from './CardBase';
import { ShareButton } from './ShareButton';
import { colors, spacing, typography } from '@/theme';
import { useSession } from '@/state/SessionProvider';
import { shareSlot } from '@/utils/shareCard';
import type { SlotCardData } from '@/state/cardsStore';

export interface SlotCardProps {
  slot: SlotCardData;
  onPress?: (s: SlotCardData) => void;
}

export function SlotCard({ slot, onPress }: SlotCardProps) {
  const { t } = useTranslation();
  const setFocusListing = useSession((s) => s.setFocusListing);

  const handlePress = () => {
    if (slot.listing_id) setFocusListing(slot.listing_id);
    onPress?.(slot);
  };

  const { day, time, range } = formatSlotTimes(slot.starts_at_iso, slot.ends_at_iso);

  return (
    <CardBase
      accent={colors.agentRealestate}
      onPress={handlePress}
      testID={`slot-card-${slot.id}`}
      topRight={<StatusPill status={slot.status} />}
      bottomBar={
        <View style={styles.actions}>
          <ShareButton
            accent={colors.agentRealestate}
            onPress={() => { void shareSlot(slot, t); }}
            testID={`share-slot-${slot.id}`}
          />
        </View>
      }
    >
      <Text style={styles.day}>{day}</Text>
      <Text style={styles.time}>{time}</Text>
      <Text style={styles.range}>{range}</Text>
      <Text style={styles.agent}>con {slot.agent_name}</Text>
    </CardBase>
  );
}

function StatusPill({ status }: { status: SlotCardData['status'] }) {
  const map: Record<SlotCardData['status'], { tone: string; label: string }> = {
    proposed: { tone: colors.gold, label: 'propuesta' },
    confirmed: { tone: colors.success, label: 'confirmada' },
    declined: { tone: colors.danger, label: 'rechazada' },
  };
  const { tone, label } = map[status];
  return (
    <View style={[styles.pill, { borderColor: tone, backgroundColor: tone + '29' }]}>
      <Text style={[styles.pillLabel, { color: tone }]}>{label}</Text>
    </View>
  );
}

function formatSlotTimes(startsIso: string, endsIso: string): {
  day: string; time: string; range: string;
} {
  try {
    const a = new Date(startsIso);
    const b = new Date(endsIso);
    const day = a.toLocaleDateString('es-MX', {
      weekday: 'long', day: 'numeric', month: 'long',
    });
    const time = a.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
    const end = b.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
    const range = `${time} – ${end}`;
    return { day, time, range };
  } catch {
    return { day: startsIso, time: '', range: '' };
  }
}

const styles = StyleSheet.create({
  day: { ...typography.h3, color: colors.textPrimary, textTransform: 'capitalize' as const },
  time: { ...typography.display, color: colors.gold },
  range: { ...typography.caption, color: colors.textMuted },
  agent: { ...typography.body, color: colors.textSecondary, marginTop: spacing.s },
  pill: {
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    borderRadius: 6,
    borderWidth: 1,
  },
  pillLabel: { ...typography.caption, fontWeight: '700' as const, textTransform: 'uppercase' as const },
  actions: { padding: spacing.m, paddingTop: 0, alignItems: 'flex-start' },
});
