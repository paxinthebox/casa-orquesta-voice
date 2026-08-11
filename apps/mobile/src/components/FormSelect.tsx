/**
 * Simple modal select for profile forms (no @react-native-picker dependency).
 */
import React, { useState } from 'react';
import {
  Modal,
  View,
  Text,
  Pressable,
  StyleSheet,
  ScrollView,
} from 'react-native';

import { colors, spacing, radii, typography } from '@/theme';

export interface FormSelectOption<T extends string> {
  value: T;
  label: string;
}

export interface FormSelectProps<T extends string> {
  label: string;
  value: T;
  options: FormSelectOption<T>[];
  onChange: (value: T) => void;
  testID?: string;
}

export function FormSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  testID,
}: FormSelectProps<T>) {
  const [open, setOpen] = useState(false);
  const selected = options.find((o) => o.value === value) ?? options[0];

  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <Pressable
        style={styles.trigger}
        onPress={() => setOpen(true)}
        accessibilityRole="button"
        testID={testID}
      >
        <Text style={styles.triggerLabel}>{selected?.label ?? value}</Text>
        <Text style={styles.chevron}>▾</Text>
      </Pressable>

      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <View style={styles.backdrop}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setOpen(false)} />
          <View style={styles.sheet}>
            <Text style={styles.sheetTitle}>{label}</Text>
            <ScrollView keyboardShouldPersistTaps="handled">
              {options.map((opt) => {
                const active = opt.value === value;
                return (
                  <Pressable
                    key={opt.value || '__empty__'}
                    style={[styles.option, active && styles.optionActive]}
                    onPress={() => {
                      onChange(opt.value);
                      setOpen(false);
                    }}
                    accessibilityRole="button"
                    testID={testID ? `${testID}-opt-${opt.value || 'empty'}` : undefined}
                  >
                    <Text style={[styles.optionLabel, active && styles.optionLabelActive]}>
                      {opt.label}
                    </Text>
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: spacing.xs },
  label: { ...typography.caption, color: colors.textSecondary },
  trigger: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: spacing.m,
    borderRadius: radii.s,
    borderWidth: 1,
    borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
  },
  triggerLabel: { ...typography.body, color: colors.textPrimary, flex: 1 },
  chevron: { color: colors.textMuted, fontSize: 14 },
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    justifyContent: 'flex-end',
  },
  sheet: {
    maxHeight: '70%',
    backgroundColor: colors.navyEl2,
    borderTopLeftRadius: radii.l,
    borderTopRightRadius: radii.l,
    padding: spacing.l,
    gap: spacing.m,
  },
  sheetTitle: { ...typography.h3, color: colors.textPrimary },
  option: {
    paddingVertical: spacing.m,
    paddingHorizontal: spacing.m,
    borderRadius: radii.s,
    marginBottom: spacing.xs,
  },
  optionActive: { backgroundColor: colors.goldFaint },
  optionLabel: { ...typography.body, color: colors.textPrimary },
  optionLabelActive: { color: colors.gold, fontWeight: '600' },
});
