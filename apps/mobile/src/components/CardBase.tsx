/**
 * CardBase — shared shell for ListingCard / SlotCard / AuditCard.
 */
import React from 'react';
import { Pressable, View, Text, StyleSheet } from 'react-native';

import { colors, spacing, radii, typography, shadow } from '@/theme';

export interface CardBaseProps {
  accent: string;
  testID?: string;
  topRight?: React.ReactNode;
  bottomBar?: React.ReactNode;
  onPress?: () => void;
  pinned?: boolean;
  children: React.ReactNode;
}

export function CardBase({
  accent, testID, topRight, bottomBar, onPress, pinned, children,
}: CardBaseProps) {
  const inner = (
    <View style={styles.inner}>
      <View style={styles.row}>
        <View style={[styles.accent, { backgroundColor: accent }]} />
        <View style={styles.body}>
          {(pinned || topRight) ? (
            <View style={styles.topRow}>
              {pinned ? <Text style={[styles.pin, { color: accent }]}>★ enfocado</Text> : <View />}
              <View>{topRight}</View>
            </View>
          ) : null}
          {children}
        </View>
      </View>
      {bottomBar ? <View style={styles.bottom}>{bottomBar}</View> : null}
    </View>
  );

  if (onPress) {
    return (
      <Pressable
        onPress={onPress}
        android_ripple={{ color: colors.navyEl2 }}
        style={({ pressed }) => [styles.card, pressed && styles.pressed]}
        testID={testID}
      >
        {inner}
      </Pressable>
    );
  }

  return <View style={styles.card} testID={testID}>{inner}</View>;
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.navyEl1,
    borderRadius: radii.l,
    borderWidth: 1,
    borderColor: colors.hairline,
    overflow: 'hidden',
    marginVertical: spacing.s,
    ...shadow.card,
  },
  inner: { flexDirection: 'column' },
  row: { flexDirection: 'row' },
  pressed: { opacity: 0.92, transform: [{ scale: 0.995 }] },
  accent: { width: 4, alignSelf: 'stretch' },
  body: { flex: 1, padding: spacing.l, gap: spacing.s },
  topRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  pin: { ...typography.caption, fontWeight: '700' as const },
  bottom: {
    borderTopWidth: 1,
    borderTopColor: colors.hairline,
    backgroundColor: colors.navyEl1,
  },
});
