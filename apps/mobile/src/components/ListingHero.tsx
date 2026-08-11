/**
 * Listing photo header — real URL when available, else state placeholder (MVP pattern).
 */
import React from 'react';
import { View, Text, Image, StyleSheet } from 'react-native';

import { colors, spacing, radii, typography } from '@/theme';

const STATE_VISUAL: Record<string, { emoji: string; colors: [string, string] }> = {
  CDMX: { emoji: '🏙️', colors: ['#1a2744', '#2d4a7a'] },
  Morelos: { emoji: '🌴', colors: ['#1a3328', '#2d6b4a'] },
};

export interface ListingHeroProps {
  state: string;
  thumbnail?: string;
  title?: string;
}

export function ListingHero({ state, thumbnail, title }: ListingHeroProps) {
  const visual = STATE_VISUAL[state] ?? { emoji: '🏠', colors: ['#1a2744', '#3a3a5c'] as [string, string] };
  const remote = thumbnail?.startsWith('http') ? thumbnail : undefined;

  if (remote) {
    return (
      <View style={styles.wrap}>
        <Image source={{ uri: remote }} style={styles.image} accessibilityLabel={title} />
      </View>
    );
  }

  return (
    <View style={[styles.wrap, styles.placeholder, { backgroundColor: visual.colors[0] }]}>
      <View style={[styles.gradientOverlay, { backgroundColor: visual.colors[1] + '55' }]} />
      <Text style={styles.emoji}>{visual.emoji}</Text>
      <Text style={styles.stateLabel}>{state}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    height: 120,
    borderRadius: radii.m,
    overflow: 'hidden',
    marginBottom: spacing.s,
  },
  image: { width: '100%', height: '100%' },
  placeholder: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  gradientOverlay: {
    ...StyleSheet.absoluteFillObject,
  },
  emoji: { fontSize: 36 },
  stateLabel: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.xs },
});
