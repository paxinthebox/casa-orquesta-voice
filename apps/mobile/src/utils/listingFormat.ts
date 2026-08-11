/**
 * Listing display helpers + record parsing (shared by cardsStore and Detail fetch).
 */
import type { ListingCardData } from '@/state/cardsStore';

const LISTINGS_BASE = (process.env.EXPO_PUBLIC_LISTINGS_URL ?? '').replace(/\/$/, '');

const LISTING_PAGE_RE =
  /inmuebles24\.com\/propiedades\/|zonaprop\.com\.mx\/propiedades\/|vivanuncios\.com\.mx\/.*\.html|\.html(?:\?|$)/i;
const IMAGE_EXT_RE = /\.(?:jpe?g|png|webp|gif|avif)(?:\?|$)/i;

/** True when URL is a photo CDN/path, not a portal listing HTML page. */
export function isListingImageUrl(url: string): boolean {
  const trimmed = url.trim();
  if (!trimmed.startsWith('http://') && !trimmed.startsWith('https://')) {
    return trimmed.startsWith('/img/') || trimmed.startsWith('/');
  }
  const lower = trimmed.toLowerCase();
  if (LISTING_PAGE_RE.test(lower)) return false;
  if (lower.includes('naventcdn.com')) return true;
  if (IMAGE_EXT_RE.test(lower)) return true;
  if (
    lower.includes('cloudinary.com') ||
    lower.includes('easybroker.com') ||
    lower.includes('picsum.photos')
  ) {
    return true;
  }
  return false;
}

function listingPhotoFallback(listingId: string): string {
  const seed = encodeURIComponent(listingId);
  return `https://picsum.photos/seed/${seed}/800/600`;
}

/** Resolve seed `/img/...` paths and ensure mobile-ready http(s) URLs. */
export function resolveListingMediaUrl(url: string, listingId?: string): string {
  const trimmed = url.trim();
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
    return isListingImageUrl(trimmed) ? trimmed : '';
  }
  if (trimmed.startsWith('/') && LISTINGS_BASE) {
    return `${LISTINGS_BASE}${trimmed}`;
  }
  if (trimmed.startsWith('/img/') || trimmed.startsWith('/')) {
    const seed = encodeURIComponent(listingId ?? trimmed);
    return `https://picsum.photos/seed/${seed}/800/600`;
  }
  return trimmed;
}

export function normalizeListingMedia(
  media: string[] | undefined,
  listingId: string,
  thumbnail?: string,
): { media?: string[]; thumbnail?: string } {
  const resolved = (media ?? [])
    .map((m) => resolveListingMediaUrl(m, listingId))
    .filter((m) => m.startsWith('http'));
  const thumbRaw = thumbnail
    ? resolveListingMediaUrl(thumbnail, listingId)
    : resolved[0];
  const httpThumb = thumbRaw?.startsWith('http') ? thumbRaw : undefined;
  const fallback = listingPhotoFallback(listingId);
  const finalThumb = httpThumb ?? fallback;
  const finalMedia = resolved.length ? resolved : [fallback];
  return {
    media: finalMedia,
    thumbnail: finalThumb,
  };
}

export function formatListingPrice(n: number, listingMode?: 'sale' | 'rent'): string {
  if (!Number.isFinite(n) || n <= 0) return '—';
  try {
    const base = new Intl.NumberFormat('es-MX', {
      style: 'currency',
      currency: 'MXN',
      maximumFractionDigits: 0,
    }).format(n);
    return listingMode === 'rent' ? `${base}/mes` : base;
  } catch {
    const base = `$${n.toLocaleString('es-MX')} MXN`;
    return listingMode === 'rent' ? `${base}/mes` : base;
  }
}

export function formatPropertyType(type?: string): string {
  if (!type) return '';
  const labels: Record<string, string> = {
    departamento: 'Departamento',
    casa: 'Casa',
    condominio: 'Condominio',
    loft: 'Loft',
    penthouse: 'Penthouse',
    estudio: 'Estudio',
    terreno: 'Terreno',
    inmueble: 'Inmueble',
  };
  return labels[type.toLowerCase()] ?? type;
}

export function formatListingMode(mode?: 'sale' | 'rent'): string {
  if (mode === 'rent') return 'Renta anual';
  if (mode === 'sale') return 'Venta';
  return '';
}

const DEMO_CATALOG_ID = /^L-(CDMX|MOR)-/i;

/** Stage-pilot seed rows (listings.json) — not portal inventory. */
export function isDemoCatalogListing(listing: {
  id: string;
  source?: string;
}): boolean {
  if (listing.source === 'catalog_demo' || listing.source === 'mock') return true;
  return DEMO_CATALOG_ID.test(listing.id);
}

export function formatSourceLabel(source?: string, listingId?: string): string {
  if (source === 'catalog_demo' || source === 'mock') return 'Catálogo demo';
  if (source === 'inmuebles24') return 'Inmuebles24';
  if (source === 'vivanuncios') return 'Vivanuncios';
  if (source === 'propiedades') return 'Propiedades.com';
  if (source === 'easybroker' || source === 'easybroker_mls') return 'EasyBroker';
  if (source === 'lamudi') return 'Lamudi';
  if (source === 'mercadolibre') return 'Mercado Libre';
  if (listingId && DEMO_CATALOG_ID.test(listingId)) return 'Catálogo demo';
  if (!source) return '';
  return source;
}

export function parseListingRecord(r: unknown, ts: number): ListingCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const id = (o.id ?? o.listing_id ?? o.folio_real) as string | undefined;
  if (!id) return null;

  const beds = typeof o.bedrooms === 'number'
    ? o.bedrooms
    : typeof o.beds === 'number'
      ? o.beds
      : undefined;
  const baths = typeof o.bathrooms === 'number'
    ? o.bathrooms
    : typeof o.baths === 'number'
      ? o.baths
      : undefined;
  const zone = (o.zone ?? o.fraccionamiento ?? o.colonia ?? o.neighborhood ?? '') as string;
  const rawScore = o.match_score ?? o.score;
  const matchScore = typeof rawScore === 'number'
    ? (rawScore <= 1 ? rawScore : rawScore / 100)
    : undefined;

  const mediaRaw = Array.isArray(o.media) ? (o.media as unknown[]) : [];
  const mediaStrings = mediaRaw.filter((m): m is string => typeof m === 'string' && m.length > 0);
  const { media, thumbnail } = normalizeListingMedia(
    mediaStrings,
    String(id),
    typeof o.thumbnail === 'string' ? o.thumbnail : undefined,
  );

  const features = Array.isArray(o.features)
    ? (o.features as unknown[]).filter((f): f is string => typeof f === 'string')
    : undefined;

  const rawMode = o.listing_mode;
  const listing_mode = rawMode === 'rent' || rawMode === 'sale' ? rawMode : undefined;

  const source_url = typeof o.source_url === 'string' && o.source_url.startsWith('http')
    ? o.source_url
    : undefined;

  return {
    kind: 'listing',
    id: String(id),
    title: (o.title ?? o.headline ?? `Propiedad ${zone}`.trim()) as string,
    price_mxn: Number(o.price_mxn ?? o.price ?? 0),
    listing_mode,
    bedrooms: beds,
    bathrooms: baths,
    m2: typeof o.m2 === 'number' ? o.m2 : undefined,
    zone,
    neighborhood: typeof o.neighborhood === 'string' ? o.neighborhood : zone || undefined,
    city: (o.city ?? 'CDMX') as string,
    state: (o.state ?? 'CDMX') as string,
    address: typeof o.address === 'string' ? o.address : undefined,
    thumbnail,
    media: media && media.length ? media : undefined,
    features: features?.length ? features : undefined,
    description: typeof o.description === 'string' && o.description.trim()
      ? o.description.trim()
      : undefined,
    type: typeof o.type === 'string' ? o.type : undefined,
    year_built: typeof o.year_built === 'number' ? o.year_built : undefined,
    status: typeof o.status === 'string' ? o.status : undefined,
    lat: typeof o.lat === 'number' ? o.lat : undefined,
    lng: typeof o.lng === 'number' ? o.lng : undefined,
    source_url,
    publisher_name: typeof o.publisher_name === 'string' && o.publisher_name.trim()
      ? o.publisher_name.trim()
      : undefined,
    agent_name: typeof o.agent_name === 'string' && o.agent_name.trim()
      ? o.agent_name.trim()
      : undefined,
    rent_term: typeof o.rent_term === 'string' ? o.rent_term : undefined,
    why: typeof o.why === 'string' ? o.why : undefined,
    match_score: matchScore,
    source: (() => {
      const raw = typeof o.source === 'string' ? o.source.trim() : '';
      if (raw) return raw;
      const lid = String(id);
      if (DEMO_CATALOG_ID.test(lid)) return 'catalog_demo';
      if (lid.startsWith('I24-')) return 'inmuebles24';
      if (lid.startsWith('VA-')) return 'vivanuncios';
      if (lid.startsWith('PROP-')) return 'propiedades';
      if (lid.startsWith('LAM-')) return 'lamudi';
      if (lid.startsWith('ML-')) return 'mercadolibre';
      return undefined;
    })(),
    alternate_sources: (() => {
      const raw = o.alternate_sources;
      if (!Array.isArray(raw)) return undefined;
      const cleaned = raw
        .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
        .map((item) => ({
          source: typeof item.source === 'string' ? item.source : undefined,
          id: typeof item.id === 'string' ? item.id : undefined,
          source_url: typeof item.source_url === 'string' && item.source_url.startsWith('http')
            ? item.source_url
            : undefined,
        }))
        .filter((item) => item.source || item.id || item.source_url);
      return cleaned.length ? cleaned : undefined;
    })(),
    source_agent: 'locator_agent',
    ts,
  };
}

/** Merge two listing snapshots — keeps the richest field values. */
export function mergeListingDetails(
  base: ListingCardData,
  incoming: ListingCardData,
): ListingCardData {
  const pick = <T>(a: T | undefined, b: T | undefined): T | undefined => {
    if (b === undefined || b === null || b === '') return a;
    if (typeof b === 'string' && typeof a === 'string' && a.length > b.length) return a;
    return b;
  };
  const descA = base.description ?? '';
  const descB = incoming.description ?? '';
  return {
    ...base,
    ...incoming,
    title: pick(base.title, incoming.title) ?? base.title,
    price_mxn: incoming.price_mxn > 0 ? incoming.price_mxn : base.price_mxn,
    description: descB.length >= descA.length ? descB : descA || descB,
    features: (incoming.features?.length ?? 0) >= (base.features?.length ?? 0)
      ? incoming.features
      : base.features ?? incoming.features,
    media: (incoming.media?.length ?? 0) >= (base.media?.length ?? 0)
      ? incoming.media
      : base.media ?? incoming.media,
    thumbnail: incoming.thumbnail ?? base.thumbnail,
    source_url: incoming.source_url ?? base.source_url,
    alternate_sources: (incoming.alternate_sources?.length ?? 0) >= (base.alternate_sources?.length ?? 0)
      ? incoming.alternate_sources
      : base.alternate_sources ?? incoming.alternate_sources,
    match_score: incoming.match_score ?? base.match_score,
    why: incoming.why ?? base.why,
    ts: Math.max(base.ts, incoming.ts),
  };
}
