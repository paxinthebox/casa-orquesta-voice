/**
 * DetailScreen — drilldown for selected listing, person, or audit finding.
 */
import React, { useEffect, useState } from 'react';
import {
  SafeAreaView,
  ScrollView,
  View,
  Text,
  Pressable,
  StyleSheet,
  Image,
  Linking,
  ActivityIndicator,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { useSession } from '@/state/SessionProvider';
import { useCardsStore, type ListingCardData } from '@/state/cardsStore';
import { ListingHero } from '@/components/ListingHero';
import { MicButton } from '@/components/MicButton';
import { ShareButton } from '@/components/ShareButton';
import { useVoice } from '@/voice/VoiceProvider';
import { PersonFollowUpPanel } from '@/components/PersonFollowUpPanel';
import { fetchListingById } from '@/api/listingsClient';
import {
  formatListingMode,
  formatListingPrice,
  formatPropertyType,
  formatSourceLabel,
  isDemoCatalogListing,
  mergeListingDetails,
} from '@/utils/listingFormat';
import { shareListing } from '@/utils/shareCard';
import { buildListingAuditPrompt } from '@/utils/listingAudit';

type Props = NativeStackScreenProps<RootStackParamList, 'Detail'>;

export function DetailScreen({ navigation, route }: Props) {
  const { t, i18n } = useTranslation();
  const { focusListing, sendFollowUpMessage, syncThreadContext } = useVoice();
  const focusListingId = useSession((s) => s.focusListingId);
  const focusDocumentId = useSession((s) => s.focusDocumentId);
  const getListingById = useCardsStore((s) => s.getListingById);
  const getAuditByDocumentId = useCardsStore((s) => s.getAuditByDocumentId);
  const getPersonById = useCardsStore((s) => s.getPersonById);

  const id = route.params?.id;
  const kind = route.params?.kind ?? (focusListingId ? 'listing' : 'audit');

  const cardListing = kind === 'listing'
    ? getListingById(id ?? focusListingId ?? '') ?? null
    : null;
  const [listing, setListing] = useState<ListingCardData | null>(cardListing);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    if (kind !== 'listing') return;
    const listingId = id ?? focusListingId ?? '';
    if (!listingId) return;
    setListing(cardListing);
    let cancelled = false;
    setLoadingDetail(true);
    void (async () => {
      const full = await fetchListingById(listingId);
      if (cancelled) return;
      if (full) {
        setListing((prev) => {
          const base = prev ?? cardListing;
          return base ? mergeListingDetails(base, full) : full;
        });
      }
      setLoadingDetail(false);
    })();
    return () => { cancelled = true; };
  }, [kind, id, focusListingId, cardListing]);

  const audit = kind === 'audit'
    ? getAuditByDocumentId(id ?? focusDocumentId ?? '') ?? null
    : null;
  const person = kind === 'people'
    ? getPersonById(id ?? '') ?? null
    : null;

  const [followUpBusy, setFollowUpBusy] = useState(false);
  const [auditBusy, setAuditBusy] = useState(false);

  const handlePersonFollowUp = async (prompt: string) => {
    setFollowUpBusy(true);
    try {
      await sendFollowUpMessage(prompt);
    } finally {
      setFollowUpBusy(false);
    }
  };

  const handleListingAudit = async (listingId: string) => {
    if (auditBusy) return;
    setAuditBusy(true);
    try {
      focusListing(listingId);
      syncThreadContext();
      await sendFollowUpMessage(buildListingAuditPrompt(i18n.language));
    } finally {
      setAuditBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.root} testID="screen-detail">
      <ScrollView contentContainerStyle={styles.scroll}>
        {listing ? (
          <ListingDetail
            listing={listing}
            loading={loadingDetail}
            onAudit={() => { void handleListingAudit(listing.id); }}
            auditBusy={auditBusy}
            onSchedule={() => navigation.navigate('VisitSchedule', {
              listingId: listing.id,
              listingTitle: listing.title,
            })}
            onShare={() => { void shareListing(listing, t); }}
            t={t}
          />
        ) : null}
        {audit ? <AuditDetail audit={audit} /> : null}
        {person ? (
          <PersonDetail
            person={person}
            onFollowUp={handlePersonFollowUp}
            followUpBusy={followUpBusy}
            t={t}
          />
        ) : null}
        {!listing && !audit && !person ? (
          <Text style={styles.empty}>{t('errors.generic')}</Text>
        ) : null}

        <Pressable
          style={styles.cta}
          onPress={() => navigation.goBack()}
          accessibilityRole="button"
        >
          <Text style={styles.ctaLabel}>{t('common.back')}</Text>
        </Pressable>
      </ScrollView>

      <View style={styles.micBar}>
        <MicButton testID="detail-mic" />
      </View>
    </SafeAreaView>
  );
}

function ListingDetail({ listing, loading, onAudit, auditBusy, onSchedule, onShare, t }: {
  listing: ListingCardData;
  loading: boolean;
  onAudit: () => void;
  auditBusy: boolean;
  onSchedule: () => void;
  onShare: () => void;
  t: ReturnType<typeof useTranslation>['t'];
}) {
  const photos = (listing.media ?? []).filter((u) => u.startsWith('http'));
  const demoCatalog = isDemoCatalogListing(listing);
  const sourceLabel = formatSourceLabel(listing.source, listing.id);
  const modeLabel = formatListingMode(listing.listing_mode);
  const typeLabel = formatPropertyType(listing.type);

  const openSource = () => {
    if (listing.source_url) void Linking.openURL(listing.source_url);
  };

  return (
    <View style={styles.section}>
      <ListingHero state={listing.state} thumbnail={listing.thumbnail} title={listing.title} />

      {demoCatalog ? (
        <View style={styles.demoBanner} testID="listing-detail-demo-banner">
          <Text style={styles.demoBannerTitle}>{t('cards.demo_catalog')}</Text>
          <Text style={styles.demoBannerBody}>{t('cards.demo_catalog_hint')}</Text>
        </View>
      ) : null}

      {photos.length > 1 ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.gallery}>
          {photos.map((uri) => (
            <Image key={uri} source={{ uri }} style={styles.galleryImg} resizeMode="cover" />
          ))}
        </ScrollView>
      ) : null}

      <View style={styles.titleRow}>
        <Text style={styles.title}>{listing.title}</Text>
        <ShareButton accent={colors.agentLocator} onPress={onShare} testID="detail-share-listing" />
      </View>

      <Text style={styles.subtitle}>
        {[listing.zone, listing.city, listing.state].filter(Boolean).join(' · ')}
      </Text>
      {listing.address ? <Text style={styles.subtitle}>{listing.address}</Text> : null}

      <View style={styles.badgeRow}>
        {modeLabel ? <Badge label={modeLabel} /> : null}
        {typeLabel ? <Badge label={typeLabel} /> : null}
        {sourceLabel ? <Badge label={sourceLabel} muted /> : null}
        {listing.match_score != null ? (
          <Badge label={`${Math.round(listing.match_score * 100)}% match`} />
        ) : null}
      </View>

      <Text style={styles.price}>
        {formatListingPrice(listing.price_mxn, listing.listing_mode)}
      </Text>

      <View style={styles.metaRow}>
        {listing.bedrooms != null && <Stat n={listing.bedrooms} label={t('detail.listing.beds')} />}
        {listing.bathrooms != null && <Stat n={listing.bathrooms} label={t('detail.listing.baths')} />}
        {listing.m2 != null && <Stat n={listing.m2} label="m²" />}
        {listing.year_built != null && <Stat n={listing.year_built} label={t('detail.listing.year')} />}
      </View>

      {loading ? (
        <View style={styles.loadingRow}>
          <ActivityIndicator color={colors.agentLocator} size="small" />
          <Text style={styles.hint}>{t('detail.listing.loading')}</Text>
        </View>
      ) : null}

      {listing.description ? (
        <View style={styles.block}>
          <Text style={styles.blockTitle}>{t('detail.listing.description')}</Text>
          <Text style={styles.body}>{listing.description}</Text>
        </View>
      ) : null}

      {listing.features?.length ? (
        <View style={styles.block}>
          <Text style={styles.blockTitle}>{t('detail.listing.features')}</Text>
          <View style={styles.tagRow}>
            {listing.features.map((f) => (
              <View key={f} style={styles.tag}><Text style={styles.tagText}>{f}</Text></View>
            ))}
          </View>
        </View>
      ) : null}

      <View style={styles.block}>
        <Text style={styles.blockTitle}>{t('detail.listing.details')}</Text>
        <DetailRow label={t('detail.listing.id')} value={listing.id} />
        {listing.status ? (
          <DetailRow label={t('detail.listing.status')} value={listing.status} />
        ) : null}
        {listing.neighborhood ? (
          <DetailRow label={t('detail.listing.neighborhood')} value={listing.neighborhood} />
        ) : null}
        {listing.publisher_name ? (
          <DetailRow label={t('detail.listing.publisher')} value={listing.publisher_name} />
        ) : null}
        {listing.agent_name ? (
          <DetailRow label={t('detail.listing.agent')} value={listing.agent_name} />
        ) : null}
        {listing.lat != null && listing.lng != null ? (
          <DetailRow
            label={t('detail.listing.coordinates')}
            value={`${listing.lat.toFixed(5)}, ${listing.lng.toFixed(5)}`}
          />
        ) : null}
      </View>

      {listing.source_url ? (
        <Pressable style={styles.linkBtn} onPress={openSource} accessibilityRole="link">
          <Text style={styles.linkBtnLabel}>
            {t('detail.listing.view_source', { source: sourceLabel || 'portal' })}
          </Text>
        </Pressable>
      ) : null}

      {listing.alternate_sources?.length ? (
        <View style={styles.block}>
          <Text style={styles.blockTitle}>{t('detail.listing.also_on')}</Text>
          {listing.alternate_sources.map((alt) => {
            const label = formatSourceLabel(alt.source, alt.id);
            if (!alt.source_url) {
              return label ? (
                <Text key={alt.id ?? label} style={styles.body}>{label}</Text>
              ) : null;
            }
            return (
              <Pressable
                key={alt.id ?? alt.source_url}
                style={styles.linkBtn}
                onPress={() => { void Linking.openURL(alt.source_url!); }}
                accessibilityRole="link"
              >
                <Text style={styles.linkBtnLabel}>
                  {t('detail.listing.view_source', { source: label || 'portal' })}
                </Text>
              </Pressable>
            );
          })}
        </View>
      ) : null}

      <Pressable style={styles.primaryBtn} onPress={onSchedule} accessibilityRole="button">
        <Text style={styles.primaryBtnLabel}>{t('visit.schedule_cta')}</Text>
      </Pressable>

      <Pressable
        style={[styles.secondaryBtn, auditBusy && styles.btnDisabled]}
        onPress={onAudit}
        disabled={auditBusy}
        accessibilityRole="button"
        testID="detail-audit-listing"
      >
        {auditBusy ? (
          <ActivityIndicator color={colors.agentAudit} size="small" />
        ) : (
          <Text style={styles.secondaryBtnLabel}>{t('detail.listing.audit_cta')}</Text>
        )}
      </Pressable>
      <Text style={styles.hint}>{t('detail.listing.audit_hint')}</Text>
    </View>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text style={styles.detailValue}>{value}</Text>
    </View>
  );
}

function Badge({ label, muted }: { label: string; muted?: boolean }) {
  return (
    <View style={[styles.badge, muted && styles.badgeMuted]}>
      <Text style={styles.badgeText}>{label}</Text>
    </View>
  );
}

function AuditDetail({ audit }: {
  audit: NonNullable<ReturnType<typeof useCardsStore.getState>['audits'][number]>;
}) {
  return (
    <View style={styles.section}>
      <Text style={styles.title}>{audit.headline}</Text>
      <Text style={styles.subtitle}>{audit.document_id}</Text>
      {audit.mock ? (
        <Text style={styles.mockNote}>
          Datos simulados (stage). SAT/INEGI/Catastro/RPP usan conectores en vivo cuando están disponibles.
        </Text>
      ) : (
        <Text style={styles.liveNote}>Fuente en vivo · registro consultado</Text>
      )}
      {audit.findings.map((f, i) => (
        <View key={i} style={styles.findingRow}>
          <View style={[
            styles.findingDot,
            f.level === 'ok' && { backgroundColor: colors.success },
            f.level === 'warn' && { backgroundColor: colors.warning },
            f.level === 'block' && { backgroundColor: colors.danger },
          ]} />
          <Text style={styles.findingLabel}>{f.label}</Text>
        </View>
      ))}
    </View>
  );
}

function PersonDetail({ person, onFollowUp, followUpBusy, t }: {
  person: NonNullable<ReturnType<typeof useCardsStore.getState>['people'][number]>;
  onFollowUp: (prompt: string) => Promise<void>;
  followUpBusy: boolean;
  t: ReturnType<typeof useTranslation>['t'];
}) {
  const kindKey = `person.followup.kind_${person.person_kind}` as const;
  return (
    <View style={styles.section}>
      <Text style={styles.subtitle}>{t(kindKey)}</Text>
      <Text style={styles.title}>{person.name}</Text>
      {person.subtitle ? <Text style={styles.subtitle}>{person.subtitle}</Text> : null}
      <Text style={styles.subtitle}>{person.location}</Text>
      <View style={styles.tagRow}>
        {person.tags.map((tag) => (
          <View key={tag} style={styles.tag}><Text style={styles.tagText}>{tag}</Text></View>
        ))}
      </View>
      <Text style={styles.hint}>{t('person.followup.detail_hint')}</Text>
      <PersonFollowUpPanel
        person={person}
        onSendFollowUp={onFollowUp}
        busy={followUpBusy}
      />
    </View>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statN}>{n}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  scroll: { padding: spacing.l, paddingBottom: 160, gap: spacing.l },
  section: {
    padding: spacing.l,
    backgroundColor: colors.navyEl1,
    borderRadius: radii.l,
    borderWidth: 1, borderColor: colors.hairline,
    gap: spacing.s,
  },
  demoBanner: {
    padding: spacing.m,
    borderRadius: radii.m,
    backgroundColor: colors.warning + '18',
    borderWidth: 1,
    borderColor: colors.warning + '55',
    gap: spacing.xs,
  },
  demoBannerTitle: { ...typography.caption, color: colors.warning, fontWeight: '700' as const },
  demoBannerBody: { ...typography.caption, color: colors.textSecondary, lineHeight: 18 },
  titleRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.s },
  title: { ...typography.h1, color: colors.textPrimary, flex: 1 },
  subtitle: { ...typography.body, color: colors.textSecondary },
  body: { ...typography.body, color: colors.textPrimary, lineHeight: 22 },
  price: { ...typography.display, color: colors.gold, marginTop: spacing.s },
  metaRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.l, marginTop: spacing.s },
  stat: { alignItems: 'center', minWidth: 56 },
  statN: { ...typography.h2, color: colors.textPrimary },
  statLabel: { ...typography.caption, color: colors.textMuted },
  badgeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  badge: {
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    borderRadius: 6, backgroundColor: colors.agentLocator + '29',
    borderWidth: 1, borderColor: colors.agentLocator,
  },
  badgeMuted: {
    backgroundColor: colors.navy,
    borderColor: colors.hairline,
  },
  badgeText: { ...typography.caption, color: colors.textPrimary, fontWeight: '600' as const },
  block: { marginTop: spacing.m, gap: spacing.xs },
  blockTitle: { ...typography.bodyBold, color: colors.textSecondary },
  tagRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  tag: {
    paddingHorizontal: spacing.s, paddingVertical: spacing.xs,
    borderRadius: 6, backgroundColor: colors.navy,
  },
  tagText: { ...typography.caption, color: colors.textPrimary },
  detailRow: {
    flexDirection: 'row', justifyContent: 'space-between', gap: spacing.m,
    paddingVertical: spacing.xs,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  detailLabel: { ...typography.caption, color: colors.textMuted, flex: 1 },
  detailValue: { ...typography.body, color: colors.textPrimary, flex: 2, textAlign: 'right' as const },
  gallery: { marginTop: spacing.s },
  galleryImg: {
    width: 160, height: 110, borderRadius: radii.m,
    marginRight: spacing.s, backgroundColor: colors.navy,
  },
  loadingRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.s, marginTop: spacing.s },
  linkBtn: {
    marginTop: spacing.m,
    padding: spacing.m,
    borderRadius: radii.m,
    borderWidth: 1,
    borderColor: colors.agentLocator,
    alignItems: 'center',
  },
  linkBtnLabel: { ...typography.bodyBold, color: colors.agentLocator },
  findingRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.s },
  findingDot: { width: 8, height: 8, borderRadius: 4 },
  findingLabel: { ...typography.body, color: colors.textPrimary, flex: 1 },
  mockNote: { ...typography.caption, color: colors.warning },
  liveNote: { ...typography.caption, color: colors.success },
  primaryBtn: {
    marginTop: spacing.l,
    padding: spacing.m,
    borderRadius: radii.pill,
    backgroundColor: colors.agentLocator,
    alignItems: 'center',
  },
  primaryBtnLabel: { ...typography.bodyBold, color: colors.navy },
  secondaryBtn: {
    marginTop: spacing.m,
    padding: spacing.m,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.agentAudit,
    alignItems: 'center',
  },
  secondaryBtnLabel: { ...typography.bodyBold, color: colors.agentAudit },
  btnDisabled: { opacity: 0.55 },
  hint: { ...typography.caption, color: colors.textMuted, marginTop: spacing.s },
  empty: { ...typography.body, color: colors.textMuted, textAlign: 'center' },
  cta: {
    alignSelf: 'center', marginTop: spacing.l,
    paddingHorizontal: spacing.xl, paddingVertical: spacing.m,
    borderRadius: radii.pill,
    borderWidth: 1, borderColor: colors.hairline,
  },
  ctaLabel: { ...typography.body, color: colors.textPrimary },
  micBar: {
    position: 'absolute', bottom: 0, left: 0, right: 0,
    padding: spacing.xl, alignItems: 'center',
  },
});
