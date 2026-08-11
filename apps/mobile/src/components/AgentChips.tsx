/**
 * AgentChips — Phase 3.3.
 *
 * Three pills that mirror the active sub-agents during a turn:
 *
 *   realestate_agent  → gold   (the supervisor that talks to the user)
 *   locator_agent     → green  (search + map narrowing)
 *   audit_agent       → purple (title / RPP / catastro checks)
 *
 * Visual states:
 *   inactive → dim, label muted
 *   active   → outlined chip with subtle glow
 *   tool     → filled chip + small spinning dot beside the agent name
 *
 * Driven by `agentTraceStore.byAgent[agent].state`. The component is
 * pure presentation — no animations on the worklet thread to keep this
 * snappy even on cheap Android devices.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

import { colors, spacing, radii, typography } from '@/theme';
import {
  useAgentTrace,
  type AgentState,
  type KnownAgent,
} from '@/state/agentTraceStore';

interface ChipSpec {
  agent: KnownAgent;
  color: string;
  i18nKey: string;
}

const SPECS: ChipSpec[] = [
  { agent: 'realestate_agent', color: colors.agentRealestate, i18nKey: 'home.agents.realestate' },
  { agent: 'locator_agent',    color: colors.agentLocator,    i18nKey: 'home.agents.locator' },
  { agent: 'audit_agent',      color: colors.agentAudit,      i18nKey: 'home.agents.audit' },
];

export function AgentChips() {
  const { t } = useTranslation();
  const byAgent = useAgentTrace((s) => s.byAgent);

  return (
    <View style={styles.row} testID="agent-chips">
      {SPECS.map(({ agent, color, i18nKey }) => {
        const status = byAgent[agent] ?? { state: 'inactive' as AgentState };
        const active = status.state !== 'inactive';
        const toolRunning = status.state === 'tool';
        return (
          <View
            key={agent}
            testID={`agent-chip-${agent}`}
            style={[
              styles.chip,
              { borderColor: color },
              active && { backgroundColor: dim(color) },
              toolRunning && { borderWidth: 2 },
            ]}
            accessibilityRole="text"
            accessibilityLabel={`${t(i18nKey)}: ${status.state}`}
          >
            <View
              style={[
                styles.dot,
                { backgroundColor: color },
                !active && styles.dotDim,
              ]}
            />
            <Text
              style={[
                styles.label,
                !active && styles.labelDim,
                active && { color: colors.textPrimary, fontWeight: '600' as const },
              ]}
            >
              {t(i18nKey)}
            </Text>
            {toolRunning ? (
              <Text style={[styles.toolHint, { color }]}>•••</Text>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

/** Faded version of a color for the active-fill background. */
function dim(hex: string): string {
  // Inject ~16 % alpha by appending a hex pair when colorHex is #RRGGBB.
  if (hex.startsWith('#') && hex.length === 7) return `${hex}29`; // 0x29 = ~16 %
  return hex;
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: spacing.s,
    flexWrap: 'wrap',
    marginTop: spacing.l,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.s,
    paddingHorizontal: spacing.l,
    paddingVertical: spacing.s,
    borderRadius: radii.pill,
    borderWidth: 1,
    backgroundColor: colors.navyEl1,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dotDim: { opacity: 0.4 },
  label: { ...typography.body, color: colors.textSecondary },
  labelDim: { color: colors.textMuted },
  toolHint: { ...typography.bodyBold, marginLeft: spacing.xs },
});
