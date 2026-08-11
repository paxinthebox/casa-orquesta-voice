/**
 * Secondary card action — opens the native share sheet.
 */
import React from 'react';
import { Pressable, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

import { colors, spacing, radii, typography } from '@/theme';

export interface ShareButtonProps {
  onPress: () => void;
  testID?: string;
  accent?: string;
}

export function ShareButton({
  onPress,
  testID,
  accent = colors.textSecondary,
}: ShareButtonProps) {
  const { t } = useTranslation();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={t('cards.share')}
      onPress={(e) => {
        e.stopPropagation?.();
        onPress();
      }}
      style={({ pressed }) => [
        styles.btn,
        { borderColor: accent, opacity: pressed ? 0.88 : 1 },
      ]}
      testID={testID}
    >
      <Text style={[styles.label, { color: accent }]}>{t('cards.share')}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: {
    paddingHorizontal: spacing.l,
    paddingVertical: spacing.s,
    borderRadius: radii.pill,
    borderWidth: 1,
    backgroundColor: 'transparent',
  },
  label: {
    ...typography.bodyBold,
  },
});
