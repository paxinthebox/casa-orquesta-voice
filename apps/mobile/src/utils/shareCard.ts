/**
 * Native share sheet payloads for feed cards (es-MX primary copy).
 */
import { Share, Platform } from 'react-native';
import type { TFunction } from 'i18next';

import type {
  AuditCardData,
  ListingCardData,
  PeopleCardData,
  SlotCardData,
} from '@/state/cardsStore';
import { formatListingPrice, formatSourceLabel } from '@/utils/listingFormat';

const BRAND = 'Casa·Orquesta';

async function openShare(message: string, title: string, url?: string): Promise<void> {
  try {
    await Share.share(
      Platform.OS === 'ios' && url
        ? { message, title, url }
        : { message, title },
    );
  } catch {
    /* user dismissed */
  }
}

function formatSlotRange(startsIso: string, endsIso: string): string {
  try {
    const a = new Date(startsIso);
    const b = new Date(endsIso);
    const day = a.toLocaleDateString('es-MX', {
      weekday: 'long', day: 'numeric', month: 'long',
    });
    const start = a.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
    const end = b.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' });
    return `${day} · ${start} – ${end}`;
  } catch {
    return `${startsIso} – ${endsIso}`;
  }
}

export async function shareListing(listing: ListingCardData, t: TFunction): Promise<void> {
  const meta: string[] = [];
  if (listing.bedrooms != null) meta.push(`${listing.bedrooms} rec`);
  if (listing.bathrooms != null) meta.push(`${listing.bathrooms} baños`);
  if (listing.m2 != null) meta.push(`${listing.m2} m²`);
  const zone = [listing.zone, listing.city, listing.state].filter(Boolean).join(', ');
  const sourceLabel = formatSourceLabel(listing.source, listing.id);
  const url = listing.source_url?.trim();
  const lines = [
    `🏠 ${listing.title}`,
    zone,
    formatListingPrice(listing.price_mxn, listing.listing_mode),
    meta.length ? meta.join(' · ') : '',
    listing.why?.trim() ?? '',
    url ?? '',
    sourceLabel && !url ? `Fuente: ${sourceLabel}` : '',
    url ? '' : `— ${BRAND}`,
  ].filter(Boolean);
  const message = Platform.OS === 'android' && url
    ? [...lines.filter((l) => l !== url), url].join('\n')
    : lines.join('\n');
  await openShare(message, t('cards.share_title_listing'), url);
}

export async function shareSlot(slot: SlotCardData, t: TFunction): Promise<void> {
  const statusKey = `cards.slot_status_${slot.status}` as const;
  const lines = [
    `📅 ${t('cards.share_slot_heading')}`,
    formatSlotRange(slot.starts_at_iso, slot.ends_at_iso),
    t('cards.share_slot_agent', { name: slot.agent_name }),
    t('cards.share_slot_status', { status: t(statusKey) }),
    `— ${BRAND}`,
  ];
  await openShare(lines.join('\n'), t('cards.share_title_slot'));
}

export async function shareAudit(audit: AuditCardData, t: TFunction): Promise<void> {
  const pct = Math.round(Math.max(0, Math.min(1, audit.score)) * 100);
  const findings = audit.findings.slice(0, 5).map((f) => `• ${f.label}`).join('\n');
  const lines = [
    `📋 ${audit.headline}`,
    audit.mock ? t('cards.share_audit_mock') : '',
    t('cards.share_audit_score', { score: pct }),
    findings,
    `— ${BRAND}`,
  ].filter(Boolean);
  await openShare(lines.join('\n'), t('cards.share_title_audit'));
}

export async function sharePerson(person: PeopleCardData, t: TFunction): Promise<void> {
  const kindKey = `cards.person_kind_${person.person_kind}` as const;
  const lines = [
    `${t(kindKey)} · ${person.name}`,
    person.subtitle ?? '',
    person.location,
    person.tags.length ? person.tags.join(' · ') : '',
    `— ${BRAND}`,
  ].filter(Boolean);
  await openShare(lines.join('\n'), t('cards.share_title_person'));
}
