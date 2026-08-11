/**
 * PeopleCard — collaborator / broker / buyer with Seleccionar CTA.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

import { CardBase } from './CardBase';
import { SelectButton } from './SelectButton';
import { ShareButton } from './ShareButton';
import { colors, spacing, typography } from '@/theme';
import { formatTimelineTs } from '@/state/cardsStore';
import { useSession } from '@/state/SessionProvider';
import { useVoice } from '@/voice/VoiceProvider';
import { sharePerson } from '@/utils/shareCard';
import type { PeopleCardData } from '@/state/cardsStore';

const ACCENT: Record<PeopleCardData['person_kind'], string> = {
  buyer: colors.agentLocator,
  collaborator: '#5B8DEF',
  broker: '#9B6BFF',
};

const KIND_LABEL: Record<PeopleCardData['person_kind'], string> = {
  buyer: 'Comprador',
  collaborator: 'Agente colaborador',
  broker: 'Broker',
};

const KIND_EMOJI: Record<PeopleCardData['person_kind'], string> = {
  buyer: '👤',
  collaborator: '🤝',
  broker: '🏢',
};

export interface PeopleCardProps {
  person: PeopleCardData;
  onSelect?: (p: PeopleCardData) => void;
}

export function PeopleCard({ person, onSelect }: PeopleCardProps) {
  const { t } = useTranslation();
  const accent = ACCENT[person.person_kind];
  const focusPersonId = useSession((s) => s.focusPersonId);
  const setFocusPerson = useSession((s) => s.setFocusPerson);
  const { focusPerson } = useVoice();
  const pinned = focusPersonId === person.id;

  const handleSelect = () => {
    const meta = { kind: person.person_kind, name: person.name };
    setFocusPerson(person.id, meta);
    focusPerson(person.id, meta);
    onSelect?.(person);
  };

  return (
    <CardBase
      accent={accent}
      pinned={pinned}
      onPress={handleSelect}
      testID={`people-card-${person.id}`}
      topRight={
        <View style={styles.topRight}>
          {person.score != null ? (
            <View style={[styles.scorePill, { borderColor: accent }]}>
              <Text style={[styles.scoreText, { color: accent }]}>
                {person.person_kind === 'buyer'
                  ? `${Math.round(person.score)}`
                  : person.score.toFixed(1)}
              </Text>
            </View>
          ) : null}
          <Text style={styles.ts}>{formatTimelineTs(person.ts)}</Text>
        </View>
      }
      bottomBar={
        <View style={styles.actions}>
          <View style={styles.selectWrap}>
            <SelectButton
              label="Seleccionar"
              accent={accent}
              onPress={handleSelect}
              testID={`select-person-${person.id}`}
            />
          </View>
          <ShareButton
            accent={accent}
            onPress={() => { void sharePerson(person, t); }}
            testID={`share-person-${person.id}`}
          />
        </View>
      }
    >
      <View style={[styles.avatar, { backgroundColor: accent + '22' }]}>
        <Text style={styles.emoji}>{KIND_EMOJI[person.person_kind]}</Text>
      </View>
      <Text style={styles.kind}>{KIND_LABEL[person.person_kind]}</Text>
      <Text style={styles.name} numberOfLines={2}>{person.name}</Text>
      {person.subtitle ? (
        <Text style={styles.subtitle} numberOfLines={2}>{person.subtitle}</Text>
      ) : null}
      <Text style={styles.location} numberOfLines={2}>{person.location}</Text>
      {person.tags.length > 0 ? (
        <View style={styles.tagRow}>
          {person.tags.slice(0, 4).map((tag) => (
            <View key={tag} style={styles.tag}>
              <Text style={styles.tagText}>{tag}</Text>
            </View>
          ))}
        </View>
      ) : null}
    </CardBase>
  );
}

const styles = StyleSheet.create({
  topRight: { alignItems: 'flex-end', gap: spacing.xs },
  avatar: {
    width: 48, height: 48, borderRadius: 24,
    alignItems: 'center', justifyContent: 'center',
    marginBottom: spacing.xs,
  },
  emoji: { fontSize: 24 },
  kind: { ...typography.caption, color: colors.textMuted, textTransform: 'uppercase' as const },
  name: { ...typography.h3, color: colors.textPrimary },
  subtitle: { ...typography.body, color: colors.textSecondary },
  location: { ...typography.body, color: colors.textSecondary },
  tagRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  tag: {
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    borderRadius: 6, backgroundColor: colors.navy,
  },
  tagText: { ...typography.caption, color: colors.textPrimary },
  scorePill: {
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    borderRadius: 6, borderWidth: 1,
  },
  scoreText: { ...typography.caption, fontWeight: '700' as const },
  ts: { ...typography.caption, color: colors.textMuted },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.s,
    padding: spacing.m,
    paddingTop: 0,
  },
  selectWrap: { flex: 1 },
});
