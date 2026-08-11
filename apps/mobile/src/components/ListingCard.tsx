/**
 * ListingCard — MVP-aligned with photo header + Seleccionar CTA.
 */
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useTranslation } from 'react-i18next';

import { CardBase } from './CardBase';
import { ListingHero } from './ListingHero';
import { SelectButton } from './SelectButton';
import { ShareButton } from './ShareButton';
import { colors, spacing, radii, typography } from '@/theme';
import { formatTimelineTs } from '@/state/cardsStore';
import {
  formatListingPrice,
  formatListingMode,
  formatPropertyType,
  formatSourceLabel,
  isDemoCatalogListing,
} from '@/utils/listingFormat';
import { useSession } from '@/state/SessionProvider';
import { shareListing } from '@/utils/shareCard';
import type { ListingCardData } from '@/state/cardsStore';

export interface ListingCardProps {
  listing: ListingCardData;
  onSelect?: (l: ListingCardData) => void;
  onPress?: (l: ListingCardData) => void;
}

export function ListingCard({ listing, onSelect, onPress }: ListingCardProps) {
  const { t } = useTranslation();
  const focused = useSession((s) => s.focusListingId);
  const setFocusListing = useSession((s) => s.setFocusListing);
  const pinned = focused === listing.id;
  const demoCatalog = isDemoCatalogListing(listing);
  const typeLabel = formatPropertyType(listing.type);
  const modeLabel = formatListingMode(listing.listing_mode);

  const handleSelect = () => {
    setFocusListing(listing.id);
    onSelect?.(listing);
    onPress?.(listing);
  };

  return (
    <CardBase
      accent={colors.agentLocator}
      pinned={pinned}
      onPress={handleSelect}
      testID={`listing-card-${listing.id}`}
      topRight={
        <View style={styles.topRight}>
          {listing.match_score != null ? (
            <View style={styles.scorePill}>
              <Text style={styles.scoreText}>
                {Math.round(listing.match_score * 100)}%
              </Text>
            </View>
          ) : null}
          <Text style={styles.ts}>{formatTimelineTs(listing.ts)}</Text>
        </View>
      }
      bottomBar={
        <View style={styles.actions}>
          <View style={styles.selectWrap}>
            <SelectButton
              label="Seleccionar"
              accent={colors.agentLocator}
              onPress={handleSelect}
              testID={`select-listing-${listing.id}`}
            />
          </View>
          <ShareButton
            accent={colors.agentLocator}
            onPress={() => { void shareListing(listing, t); }}
            testID={`share-listing-${listing.id}`}
          />
        </View>
      }
    >
      <ListingHero
        state={listing.state}
        thumbnail={listing.thumbnail}
        title={listing.title}
      />
      {demoCatalog ? (
        <View style={styles.demoPill} testID={`listing-demo-${listing.id}`}>
          <Text style={styles.demoPillText}>{t('cards.demo_catalog')}</Text>
        </View>
      ) : null}
      <Text style={styles.title} numberOfLines={2}>{listing.title}</Text>
      {typeLabel || modeLabel ? (
        <View style={styles.badgeRow}>
          {typeLabel ? <Text style={styles.typeBadge}>{typeLabel}</Text> : null}
          {modeLabel ? <Text style={styles.modeBadge}>{modeLabel}</Text> : null}
        </View>
      ) : null}
      <Text style={styles.zone}>
        {[formatSourceLabel(listing.source, listing.id) || null,
          listing.zone, listing.city, listing.state].filter(Boolean).join(' · ')}
      </Text>
      <View style={styles.metaRow}>
        {listing.bedrooms != null && <Meta label="rec" value={String(listing.bedrooms)} />}
        {listing.bathrooms != null && <Meta label="baños" value={String(listing.bathrooms)} />}
        {listing.m2 != null && <Meta label="m²" value={String(listing.m2)} />}
      </View>
      {listing.why ? <Text style={styles.why}>{listing.why.trim()}</Text> : null}
      <Text style={styles.price}>
        {formatListingPrice(listing.price_mxn, listing.listing_mode)}
      </Text>
    </CardBase>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metaPill}>
      <Text style={styles.metaValue}>{value}</Text>
      <Text style={styles.metaLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  topRight: { alignItems: 'flex-end', gap: spacing.xs },
  demoPill: {
    alignSelf: 'flex-start',
    marginTop: spacing.xs,
    paddingHorizontal: spacing.s,
    paddingVertical: 2,
    borderRadius: radii.pill,
    backgroundColor: colors.warning + '24',
    borderWidth: 1,
    borderColor: colors.warning + '66',
  },
  demoPillText: {
    ...typography.caption,
    color: colors.warning,
    fontWeight: '600' as const,
  },
  title: { ...typography.h3, color: colors.textPrimary },
  badgeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  typeBadge: {
    ...typography.caption,
    color: colors.agentLocator,
    fontWeight: '600' as const,
    paddingHorizontal: spacing.s,
    paddingVertical: 2,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: colors.agentLocator,
    overflow: 'hidden',
  },
  modeBadge: {
    ...typography.caption,
    color: colors.textSecondary,
    paddingHorizontal: spacing.s,
    paddingVertical: 2,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: colors.hairline,
  },
  zone: { ...typography.body, color: colors.textSecondary },
  metaRow: { flexDirection: 'row', gap: spacing.s, marginTop: spacing.s },
  metaPill: {
    flexDirection: 'row', alignItems: 'baseline', gap: 4,
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    backgroundColor: colors.navy, borderRadius: 6,
  },
  metaValue: { ...typography.bodyBold, color: colors.textPrimary },
  metaLabel: { ...typography.caption, color: colors.textMuted, textTransform: 'lowercase' as const },
  why: { ...typography.caption, color: colors.success, marginTop: spacing.xs },
  price: { ...typography.h2, color: colors.gold, marginTop: spacing.s },
  scorePill: {
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    backgroundColor: colors.agentLocator + '29',
    borderRadius: 6, borderWidth: 1, borderColor: colors.agentLocator,
  },
  scoreText: { ...typography.caption, color: colors.agentLocator, fontWeight: '700' as const },
  ts: { ...typography.caption, color: colors.textMuted },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.s,
    padding: spacing.m,
    paddingTop: 0,
  },
  selectWrap: { flex: 1 },
});
