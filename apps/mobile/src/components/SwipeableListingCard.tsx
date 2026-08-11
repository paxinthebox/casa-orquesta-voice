/**
 * Horizontal swipe on listing cards: left = discard, right = save.
 */
import React, { useCallback } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, {
  runOnJS,
  useAnimatedStyle,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import * as Haptics from 'expo-haptics';
import { useTranslation } from 'react-i18next';

import { colors, radii, spacing, typography } from '@/theme';

const SWIPE_THRESHOLD = 96;
const OFF_SCREEN = 420;

export interface SwipeableListingCardProps {
  children: React.ReactNode;
  onSave: () => void;
  onDiscard: () => void;
  testID?: string;
}

export function SwipeableListingCard({
  children,
  onSave,
  onDiscard,
  testID,
}: SwipeableListingCardProps) {
  const { t } = useTranslation();
  const translateX = useSharedValue(0);
  const dismissed = useSharedValue(false);

  const finishSave = useCallback(() => {
    void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    onSave();
  }, [onSave]);

  const finishDiscard = useCallback(() => {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    onDiscard();
  }, [onDiscard]);

  const pan = Gesture.Pan()
    .activeOffsetX([-18, 18])
    .failOffsetY([-12, 12])
    .onUpdate((event) => {
      if (dismissed.value) return;
      translateX.value = event.translationX;
    })
    .onEnd((event) => {
      if (dismissed.value) return;
      if (event.translationX >= SWIPE_THRESHOLD) {
        dismissed.value = true;
        translateX.value = withTiming(OFF_SCREEN, { duration: 220 }, (finished) => {
          if (finished) runOnJS(finishSave)();
        });
        return;
      }
      if (event.translationX <= -SWIPE_THRESHOLD) {
        dismissed.value = true;
        translateX.value = withTiming(-OFF_SCREEN, { duration: 220 }, (finished) => {
          if (finished) runOnJS(finishDiscard)();
        });
        return;
      }
      translateX.value = withSpring(0, { damping: 18, stiffness: 220 });
    });

  const cardStyle = useAnimatedStyle(() => ({
    transform: [
      { translateX: translateX.value },
      { rotate: `${translateX.value / 24}deg` },
    ],
  }));

  const saveOverlayStyle = useAnimatedStyle(() => ({
    opacity: Math.min(1, Math.max(0, translateX.value / SWIPE_THRESHOLD)),
  }));

  const discardOverlayStyle = useAnimatedStyle(() => ({
    opacity: Math.min(1, Math.max(0, -translateX.value / SWIPE_THRESHOLD)),
  }));

  return (
    <View style={styles.shell} testID={testID}>
      <Animated.View style={[styles.overlay, styles.saveOverlay, saveOverlayStyle]}>
        <Text style={styles.overlayLabel}>{t('cards.swipe_save')}</Text>
      </Animated.View>
      <Animated.View style={[styles.overlay, styles.discardOverlay, discardOverlayStyle]}>
        <Text style={styles.overlayLabel}>{t('cards.swipe_discard')}</Text>
      </Animated.View>
      <GestureDetector gesture={pan}>
        <Animated.View style={cardStyle}>{children}</Animated.View>
      </GestureDetector>
    </View>
  );
}

const styles = StyleSheet.create({
  shell: {
    position: 'relative',
    marginVertical: spacing.xs,
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    borderRadius: radii.l,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
  },
  saveOverlay: {
    backgroundColor: 'rgba(63, 181, 138, 0.22)',
    borderColor: colors.success,
  },
  discardOverlay: {
    backgroundColor: 'rgba(216, 85, 63, 0.22)',
    borderColor: colors.danger,
  },
  overlayLabel: {
    ...typography.bodyBold,
    color: colors.textPrimary,
    letterSpacing: 0.4,
  },
});
