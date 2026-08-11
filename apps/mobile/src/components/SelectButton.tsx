/**
 * Primary CTA — mirrors MVP buyer.html "Seleccionar" on listing/people cards.
 */
import React from 'react';
import { Pressable, Text, StyleSheet } from 'react-native';

import { colors, spacing, radii, typography } from '@/theme';

export interface SelectButtonProps {
  label?: string;
  onPress: () => void;
  testID?: string;
  accent?: string;
}

export function SelectButton({
  label = 'Seleccionar',
  onPress,
  testID,
  accent = colors.gold,
}: SelectButtonProps) {
  return (
    <Pressable
      accessibilityRole="button"
      onPress={(e) => {
        e.stopPropagation?.();
        onPress();
      }}
      style={({ pressed }) => [
        styles.btn,
        { backgroundColor: accent, opacity: pressed ? 0.88 : 1 },
      ]}
      testID={testID}
    >
      <Text style={styles.label}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: {
    paddingHorizontal: spacing.l,
    paddingVertical: spacing.s,
    borderRadius: radii.pill,
    alignSelf: 'flex-start',
  },
  label: {
    ...typography.bodyBold,
    color: colors.navy,
  },
});
