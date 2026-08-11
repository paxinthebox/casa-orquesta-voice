/**
 * AuditCard — Phase 3.3.
 *
 * Visual companion to an `audit_agent.verify_title` (or sibling) result.
 * Shows the headline + a colored findings strip + an overall score.
 *
 * Tapping it pins the audited document id in the session store so the
 * next voice turn can be narrowed ("explícame la hipoteca que aparece").
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
import { shareAudit } from '@/utils/shareCard';
import type { AuditCardData } from '@/state/cardsStore';

export interface AuditCardProps {
  audit: AuditCardData;
  onPress?: (a: AuditCardData) => void;
}

export function AuditCard({ audit, onPress }: AuditCardProps) {
  const { t } = useTranslation();
  const focused = useSession((s) => s.focusDocumentId);
  const setFocusDocument = useSession((s) => s.setFocusDocument);

  const pinned = focused === audit.document_id;

  const handlePress = () => {
    setFocusDocument(audit.document_id);
    onPress?.(audit);
  };

  return (
    <CardBase
      accent={colors.agentAudit}
      pinned={pinned}
      onPress={handlePress}
      testID={`audit-card-${audit.id}`}
      topRight={
        <View style={styles.topRight}>
          <ScoreRing score={audit.score} />
          <Text style={styles.ts}>{formatTimelineTs(audit.ts)}</Text>
        </View>
      }
      bottomBar={
        <View style={styles.actions}>
          <View style={styles.selectWrap}>
            <SelectButton
              label="Seleccionar"
              accent={colors.agentAudit}
              onPress={handlePress}
              testID={`select-audit-${audit.id}`}
            />
          </View>
          <ShareButton
            accent={colors.agentAudit}
            onPress={() => { void shareAudit(audit, t); }}
            testID={`share-audit-${audit.id}`}
          />
        </View>
      }
    >
      <Text style={styles.topic}>{topicLabel(audit.topic)}</Text>
      {audit.mock ? (
        <Text style={styles.mockBadge}>Demo · registro simulado</Text>
      ) : (
        <Text style={styles.liveBadge}>Fuente en vivo</Text>
      )}
      <Text style={styles.headline}>{audit.headline}</Text>
      <View style={styles.findingsRow}>
        {audit.findings.slice(0, 3).map((f, i) => (
          <View
            key={i}
            style={[
              styles.finding,
              f.level === 'ok' && styles.findingOk,
              f.level === 'warn' && styles.findingWarn,
              f.level === 'block' && styles.findingBlock,
            ]}
          >
            <Text style={styles.findingLabel} numberOfLines={1}>{f.label}</Text>
          </View>
        ))}
        {audit.findings.length > 3 ? (
          <Text style={styles.moreFindings}>+{audit.findings.length - 3}</Text>
        ) : null}
      </View>
    </CardBase>
  );
}

function ScoreRing({ score }: { score: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const tone = pct >= 80 ? colors.success : pct >= 50 ? colors.warning : colors.danger;
  return (
    <View style={[styles.ring, { borderColor: tone }]}>
      <Text style={[styles.ringText, { color: tone }]}>{pct}</Text>
    </View>
  );
}

function topicLabel(topic: AuditCardData['topic']): string {
  switch (topic) {
    case 'title': return 'Título · RPP';
    case 'tax': return 'Predial · Catastro';
    case 'contract': return 'Contrato';
    case 'inegi': return 'INEGI · entorno';
    case 'sat': return 'SAT · RFC';
    default: return 'Verificación';
  }
}

const styles = StyleSheet.create({
  topRight: { alignItems: 'flex-end', gap: spacing.xs },
  ts: { ...typography.caption, color: colors.textMuted },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.s,
    padding: spacing.m,
    paddingTop: 0,
  },
  selectWrap: { flex: 1 },
  mockBadge: { ...typography.caption, color: colors.warning },
  liveBadge: { ...typography.caption, color: colors.success },
  topic: {
    ...typography.caption, color: colors.agentAudit,
    textTransform: 'uppercase' as const, letterSpacing: 1.2,
  },
  headline: { ...typography.h3, color: colors.textPrimary, marginTop: 2 },
  findingsRow: {
    flexDirection: 'row', gap: spacing.s, flexWrap: 'wrap',
    marginTop: spacing.s,
  },
  finding: {
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    borderRadius: 6, borderWidth: 1,
  },
  findingOk: { borderColor: colors.success, backgroundColor: colors.success + '29' },
  findingWarn: { borderColor: colors.warning, backgroundColor: colors.warning + '29' },
  findingBlock: { borderColor: colors.danger, backgroundColor: colors.danger + '29' },
  findingLabel: { ...typography.caption, color: colors.textPrimary, maxWidth: 110 },
  moreFindings: { ...typography.caption, color: colors.textMuted, alignSelf: 'center' },
  ring: {
    width: 36, height: 36, borderRadius: 18,
    borderWidth: 2,
    alignItems: 'center', justifyContent: 'center',
  },
  ringText: { ...typography.bodyBold },
});
