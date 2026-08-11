import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

import { colors, spacing, radii, typography } from '@/theme';
import { formatTimelineTs } from '@/state/cardsStore';

export interface MessageBubbleProps {
  role: 'user' | 'assistant';
  text: string;
  ts: number;
}

export function MessageBubble({ role, text, ts }: MessageBubbleProps) {
  const isUser = role === 'user';
  return (
    <View
      style={[styles.row, isUser ? styles.rowUser : styles.rowAssistant]}
      testID={`msg-${role}`}
    >
      <View style={[styles.bubble, isUser ? styles.userBubble : styles.assistantBubble]}>
        <Text style={styles.text}>{text}</Text>
        <Text style={styles.ts}>{formatTimelineTs(ts)}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { marginVertical: spacing.xs },
  rowUser: { alignItems: 'flex-end' },
  rowAssistant: { alignItems: 'flex-start' },
  bubble: {
    maxWidth: '92%',
    padding: spacing.m,
    borderRadius: radii.l,
    borderWidth: 1,
    gap: spacing.xs,
  },
  userBubble: {
    backgroundColor: colors.navyEl1,
    borderColor: colors.gold,
  },
  assistantBubble: {
    backgroundColor: colors.navyEl1,
    borderColor: colors.hairline,
  },
  text: { ...typography.body, color: colors.textPrimary },
  ts: { ...typography.caption, color: colors.textMuted, alignSelf: 'flex-end' },
});
