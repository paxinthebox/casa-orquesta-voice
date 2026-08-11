/**
 * Saved listing matches for the active client thread.
 */
import React from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

import { colors, spacing, radii, typography } from '@/theme';
import { ListingCard } from '@/components/ListingCard';
import type { ListingCardData } from '@/state/cardsStore';

export interface SavedListingsSectionProps {
  listings: ListingCardData[];
  onUnsave: (listingId: string) => void;
  onOpenListing: (listing: ListingCardData) => void;
}

export function SavedListingsSection({
  listings,
  onUnsave,
  onOpenListing,
}: SavedListingsSectionProps) {
  const { t } = useTranslation();
  if (listings.length === 0) return null;

  return (
    <View style={styles.wrap} testID="saved-listings-section">
      <Text style={styles.title}>
        {t('cards.saved_title', { count: listings.length })}
      </Text>
      <Text style={styles.subtitle}>{t('cards.saved_subtitle')}</Text>
      {listings.map((listing) => (
        <View key={listing.id} style={styles.item}>
          <ListingCard
            listing={listing}
            onPress={onOpenListing}
          />
          <Pressable
            style={styles.unsaveBtn}
            onPress={() => onUnsave(listing.id)}
            accessibilityRole="button"
            testID={`unsave-listing-${listing.id}`}
          >
            <Text style={styles.unsaveLabel}>{t('cards.unsave')}</Text>
          </Pressable>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    marginTop: spacing.l,
    padding: spacing.m,
    borderRadius: radii.l,
    backgroundColor: colors.navyEl1,
    borderWidth: 1,
    borderColor: colors.success + '55',
    gap: spacing.s,
  },
  title: {
    ...typography.bodyBold,
    color: colors.success,
  },
  subtitle: {
    ...typography.caption,
    color: colors.textMuted,
    marginBottom: spacing.xs,
  },
  item: { gap: spacing.xs },
  unsaveBtn: {
    alignSelf: 'flex-start',
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.s,
  },
  unsaveLabel: {
    ...typography.caption,
    color: colors.textSecondary,
    textDecorationLine: 'underline',
  },
});
