/**
 * MicButton — Phase 3.3.
 *
 * Push-to-talk control with a pulse halo (while listening) and an RMS
 * waveform ring (while speaking). All animations are Reanimated v3 +
 * `useDerivedValue` so they run on the UI thread and don't stutter
 * while the JS thread is busy parsing SSE.
 *
 * Behavior matrix:
 *   idle      → solid gold disc, label "Mantén presionado para hablar"
 *   listening → green disc, pulse halo 1.0 → 1.25 → 1.0 at 1.4 s
 *   thinking  → blue-ish disc, no pulse, 3-dot bouncing label
 *   speaking  → blue disc, waveform ring driven by rmsIn
 *   error     → red disc, no animation
 *
 * Tap-while-speaking calls `cancel()` so the user can barge-in by
 * touching the button (in addition to acoustic barge-in handled by
 * the server's `BargeInDetector`).
 *
 * Production note: We feature-detect Reanimated. If the user is on a
 * test/Jest environment where Reanimated isn't initialized, we fall
 * back to a static disc and never crash.
 */
import React, { useEffect, useMemo, useRef } from 'react';
import {
  Pressable,
  Text,
  View,
  StyleSheet,
  Platform,
  type GestureResponderEvent,
} from 'react-native';
import { useTranslation } from 'react-i18next';

import { colors, spacing, radii, typography, shadow, motion } from '@/theme';
import { useVoice, type VoiceUiStatus } from '@/voice/VoiceProvider';

// Reanimated is optional at runtime — we feature-detect so the component
// is renderable in plain test environments.
let Reanimated: typeof import('react-native-reanimated') | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- runtime feature-detect
  Reanimated = require('react-native-reanimated');
} catch {
  Reanimated = null;
}

const TONE_BY_STATUS: Record<VoiceUiStatus, string> = {
  idle: colors.micIdle,
  listening: colors.micListening,
  thinking: colors.micSpeaking,
  speaking: colors.micSpeaking,
  error: colors.micError,
};

const LABEL_KEY_BY_STATUS: Record<VoiceUiStatus, string> = {
  idle: 'home.mic_idle',
  listening: 'home.mic_listening',
  thinking: 'home.mic_thinking',
  speaking: 'home.mic_speaking',
  error: 'errors.voice_failed',
};

export interface MicButtonProps {
  onPressInOverride?: () => void | Promise<void>;
  onPressOutOverride?: () => void | Promise<void>;
  testID?: string;
}

const BUTTON_SIZE = 96;
const HALO_SIZE = 156;
const WAVE_BARS = 12;

export function MicButton({ onPressInOverride, onPressOutOverride, testID }: MicButtonProps) {
  const { t } = useTranslation();
  const { status, startPTT, endPTT, cancel, rmsIn } = useVoice();
  /** Tracks an active press-and-hold — avoids stale `status` in onPressOut. */
  const pttHeldRef = useRef(false);

  const tone = TONE_BY_STATUS[status];
  const label = t(LABEL_KEY_BY_STATUS[status]);

  const handlePressIn = async (_e: GestureResponderEvent) => {
    if (status === 'speaking') {
      pttHeldRef.current = false;
      cancel();
      return;
    }
    if (status === 'thinking') return;
    pttHeldRef.current = true;
    if (onPressInOverride) await onPressInOverride();
    else await startPTT();
  };

  const handlePressOut = async (_e: GestureResponderEvent) => {
    if (!pttHeldRef.current) return;
    pttHeldRef.current = false;
    if (onPressOutOverride) await onPressOutOverride();
    else await endPTT();
  };

  return (
    <View style={styles.wrap}>
      {Reanimated ? (
        <AnimatedHalo status={status} rmsIn={rmsIn} />
      ) : (
        <View style={styles.haloStatic} />
      )}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityState={{ busy: status !== 'idle' }}
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        style={({ pressed }) => [
          styles.button,
          { backgroundColor: tone },
          pressed && styles.buttonPressed,
        ]}
        testID={testID ?? 'mic-button'}
      >
        <Text style={styles.dot}>●</Text>
      </Pressable>
      <Text style={styles.label} accessibilityElementsHidden>{label}</Text>
    </View>
  );
}

/**
 * The pulse + waveform layer. Lives in its own component so the
 * (optional) Reanimated hook calls don't run when the library is
 * missing — React doesn't allow conditional hook calls in the parent.
 */
function AnimatedHalo({ status, rmsIn }: { status: VoiceUiStatus; rmsIn: number }) {
  // Type assertion is fine because this component is only rendered when
  // `Reanimated` is non-null.
  const RA = Reanimated as typeof import('react-native-reanimated');
  const { useSharedValue, useAnimatedStyle, withRepeat, withTiming,
          cancelAnimation, default: AnimatedDefault } = RA;
  const AnimatedView = AnimatedDefault.View;

  const pulse = useSharedValue(0);
  const level = useSharedValue(0);

  // Drive the pulse only while listening.
  useEffect(() => {
    if (status === 'listening') {
      pulse.value = withRepeat(
        withTiming(1, { duration: motion.slow * 3.5 }),
        -1,
        true,
      );
    } else {
      cancelAnimation(pulse);
      pulse.value = withTiming(0, { duration: motion.normal });
    }
  }, [status, pulse, cancelAnimation, withRepeat, withTiming]);

  // Smoothly track rmsIn (avoids hard jumps on noisy mic frames).
  useEffect(() => {
    level.value = withTiming(rmsIn, { duration: motion.fast });
  }, [rmsIn, level, withTiming]);

  const haloStyle = useAnimatedStyle(() => {
    const scale = 1 + pulse.value * 0.18;
    const opacity = 0.35 - pulse.value * 0.30;
    return { transform: [{ scale }], opacity };
  });

  const bars = useMemo(() => Array.from({ length: WAVE_BARS }, (_, i) => i), []);

  // Only render the wave ring while speaking — otherwise it's a static
  // halo behind the button.
  const showWave = status === 'speaking';

  return (
    <View style={styles.haloWrap} pointerEvents="none">
      <AnimatedView style={[styles.halo, haloStyle]} />
      {showWave && bars.map((i) => (
        <WaveBar key={i} idx={i} total={WAVE_BARS} level={level} RA={RA} />
      ))}
    </View>
  );
}

function WaveBar({
  idx, total, level, RA,
}: {
  idx: number;
  total: number;
  level: import('react-native-reanimated').SharedValue<number>;
  RA: typeof import('react-native-reanimated');
}) {
  const { useAnimatedStyle, default: AnimatedDefault } = RA;
  const AnimatedView = AnimatedDefault.View;
  const angle = (idx / total) * Math.PI * 2;
  // Each bar's amplitude is staggered by index so the ring "rotates".
  const phase = idx / total;

  const style = useAnimatedStyle(() => {
    'worklet';
    // Make every other bar respond a touch more, otherwise the ring
    // looks flat at low levels.
    const local = Math.min(1, level.value * (1.05 + phase * 0.25));
    const height = 8 + local * 22;
    return {
      height,
      transform: [
        { translateX: Math.cos(angle) * (HALO_SIZE / 2 - 8) },
        { translateY: Math.sin(angle) * (HALO_SIZE / 2 - 8) },
        { rotate: `${(angle * 180) / Math.PI + 90}deg` },
      ],
    };
  });

  return <AnimatedView style={[styles.waveBar, style]} />;
}

const styles = StyleSheet.create({
  wrap: { alignItems: 'center', gap: spacing.s, paddingVertical: spacing.l },
  button: {
    width: BUTTON_SIZE, height: BUTTON_SIZE, borderRadius: radii.pill,
    alignItems: 'center', justifyContent: 'center',
    ...shadow.fab,
  },
  buttonPressed: { transform: [{ scale: 0.96 }], opacity: 0.92 },
  dot: { fontSize: 28, color: colors.ink, fontWeight: '900' as const },
  label: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.s },
  haloWrap: {
    position: 'absolute', top: 0, alignSelf: 'center',
    width: HALO_SIZE, height: HALO_SIZE,
    alignItems: 'center', justifyContent: 'center',
    marginTop: spacing.l + (BUTTON_SIZE - HALO_SIZE) / 2,
  },
  halo: {
    position: 'absolute',
    width: HALO_SIZE, height: HALO_SIZE,
    borderRadius: HALO_SIZE / 2,
    backgroundColor: colors.gold,
    opacity: 0,
  },
  haloStatic: {
    position: 'absolute', top: spacing.l + (BUTTON_SIZE - HALO_SIZE) / 2,
    width: HALO_SIZE, height: HALO_SIZE, borderRadius: HALO_SIZE / 2,
    backgroundColor: colors.goldFaint,
  },
  waveBar: {
    position: 'absolute',
    width: 3,
    backgroundColor: Platform.select({
      ios: colors.micSpeaking,
      android: colors.micSpeaking,
      default: colors.micSpeaking,
    }),
    borderRadius: 1.5,
    opacity: 0.8,
  },
});
