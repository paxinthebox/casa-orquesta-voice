/**
 * cardsStore — Phase 3.3 + MVP-aligned timeline feed.
 *
 * Chronological timeline (messages + cards) scoped per client thread.
 * Cards arrive from orchestrator `tool_result` events and audit bundles
 * on `run_end.detail.data` (RPP / Catastro / INEGI).
 */
import { create } from 'zustand';

import type { AgentTraceStep } from '@/voice/VoiceClient';
import { mergeListingDetails, parseListingRecord } from '@/utils/listingFormat';
import { normalizeListingMatchFeed } from '@/utils/listingMatch';
import { useThreadsStore, type ThreadFeedSnapshot } from '@/state/threadsStore';

// ---------------------------------------------------------------------------
// Card types
// ---------------------------------------------------------------------------
export interface ListingCardData {
  kind: 'listing';
  id: string;
  title: string;
  price_mxn: number;
  listing_mode?: 'sale' | 'rent';
  bedrooms?: number;
  bathrooms?: number;
  m2?: number;
  zone: string;
  neighborhood?: string;
  city: string;
  state: string;
  address?: string;
  thumbnail?: string;
  media?: string[];
  description?: string;
  type?: string;
  year_built?: number;
  status?: string;
  lat?: number;
  lng?: number;
  source_url?: string;
  publisher_name?: string;
  agent_name?: string;
  rent_term?: string;
  features?: string[];
  why?: string;
  match_score?: number;
  source?: string;
  alternate_sources?: Array<{ source?: string; id?: string; source_url?: string }>;
  source_agent: 'locator_agent';
  ts: number;
}

export interface SlotCardData {
  kind: 'slot';
  id: string;
  listing_id?: string;
  starts_at_iso: string;
  ends_at_iso: string;
  agent_name: string;
  status: 'proposed' | 'confirmed' | 'declined';
  source_agent: 'realestate_agent';
  ts: number;
}

export interface AuditCardData {
  kind: 'audit';
  id: string;
  document_id: string;
  listing_id?: string;
  topic: 'title' | 'tax' | 'contract' | 'inegi' | 'sat';
  headline: string;
  score: number;
  findings: { label: string; level: 'ok' | 'warn' | 'block' }[];
  source_agent: 'audit_agent';
  ts: number;
  mock?: boolean;
}

export interface PeopleCardData {
  kind: 'people';
  person_kind: 'buyer' | 'collaborator' | 'broker';
  id: string;
  name: string;
  subtitle?: string;
  location: string;
  tags: string[];
  score?: number;
  source_agent: 'locator_agent';
  ts: number;
}

export type CardData = ListingCardData | SlotCardData | AuditCardData | PeopleCardData;

export type TimelineMessage = {
  kind: 'message';
  id: string;
  role: 'user' | 'assistant';
  text: string;
  /** Wall-clock ms — for display only; use `seq` for ordering. */
  ts: number;
  /** Monotonic insertion order within the session feed. */
  seq: number;
};

export type TimelineCard = {
  kind: 'card';
  id: string;
  card: CardData;
  ts: number;
  seq: number;
};

export type TimelineItem = TimelineMessage | TimelineCard;

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------
const MAX_PER_TYPE = 24;
const MAX_TIMELINE = 80;

interface CardsStore {
  /** Active thread feed (mirrors threadsStore for reactive UI). */
  timeline: TimelineItem[];
  timelineSeq: number;
  listings: ListingCardData[];
  slots: SlotCardData[];
  audits: AuditCardData[];
  people: PeopleCardData[];
  savedListings: ListingCardData[];
  discardedListingIds: string[];
  syncFromActiveThread: () => void;
  appendMessage: (
    role: 'user' | 'assistant',
    text: string,
    ts?: number,
    threadId?: string,
  ) => void;
  ingestEvent: (ev: AgentTraceStep, threadId?: string) => void;
  /** Fallback when run_end carries results but tool_result was missed. */
  ingestSearchResults: (results: unknown[], threadId?: string) => void;
  /** Flush cards received during the turn — call after the assistant bubble. */
  commitPendingTimelineCards: (threadId?: string) => void;
  saveListing: (listing: ListingCardData) => void;
  discardListing: (listingId: string) => void;
  unsaveListing: (listingId: string) => void;
  /** Dedupes repeated tool_result deliveries (e.g. reconnect / strict mode). */
  lastIngestKey: string;
  clearActive: () => void;

  getListingById: (id: string) => ListingCardData | undefined;
  getSlotById: (id: string) => SlotCardData | undefined;
  getAuditByDocumentId: (documentId: string) => AuditCardData | undefined;
  getPersonById: (id: string) => PeopleCardData | undefined;
}

function feedToStoreSlice(feed: ThreadFeedSnapshot): Pick<
  CardsStore,
  | 'timeline'
  | 'timelineSeq'
  | 'listings'
  | 'slots'
  | 'audits'
  | 'people'
  | 'savedListings'
  | 'discardedListingIds'
> {
  const match = normalizeListingMatchFeed(feed);
  return {
    timeline: feed.timeline,
    timelineSeq: feed.timelineSeq,
    listings: feed.listings,
    slots: feed.slots,
    audits: feed.audits,
    people: feed.people,
    savedListings: match.savedListings,
    discardedListingIds: match.discardedListingIds,
  };
}

function patchListingMatchFeed(
  feed: ThreadFeedSnapshot,
): ThreadFeedSnapshot {
  const match = normalizeListingMatchFeed(feed);
  if (
    feed.savedListings === match.savedListings
    && feed.discardedListingIds === match.discardedListingIds
  ) {
    return feed;
  }
  return { ...feed, ...match };
}

/** Pull listing rows from a voice-gateway run_end detail payload. */
export function extractRunResultsFromRunEnd(detail: Record<string, unknown>): unknown[] {
  const data = detail.data;
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    const results = (data as { results?: unknown[] }).results;
    if (Array.isArray(results) && results.length) return results;
  }
  const state = detail.state;
  if (state && typeof state === 'object' && !Array.isArray(state)) {
    const last = (state as { last_candidates?: unknown[] }).last_candidates;
    if (Array.isArray(last) && last.length) return last;
  }
  const top = detail.last_candidates;
  if (Array.isArray(top) && top.length) return top;
  return [];
}

function syncThreadFeed(threadId?: string): void {
  const activeId = useThreadsStore.getState().activeThreadId;
  if (!threadId || threadId === activeId) {
    useCardsStore.getState().syncFromActiveThread();
  }
}

function resolveThreadId(threadId?: string): string | null {
  return threadId ?? useThreadsStore.getState().activeThreadId;
}

function allFeeds(): ThreadFeedSnapshot[] {
  return Object.values(useThreadsStore.getState().feeds);
}

export const useCardsStore = create<CardsStore>((set, get) => ({
  timeline: [],
  timelineSeq: 0,
  listings: [],
  slots: [],
  audits: [],
  people: [],
  savedListings: [],
  discardedListingIds: [],
  lastIngestKey: '',

  syncFromActiveThread: () => {
    set(feedToStoreSlice(patchListingMatchFeed(useThreadsStore.getState().getActiveFeed())));
  },

  appendMessage: (role, text, ts = Date.now(), threadId) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const tid = resolveThreadId(threadId);
    if (!tid) return;
    useThreadsStore.getState().patchFeed(tid, (feed) => {
      const last = feed.timeline[feed.timeline.length - 1];
      if (
        last?.kind === 'message'
        && last.role === role
        && last.text.trim() === trimmed
      ) {
        return feed;
      }
      const seq = feed.timelineSeq + 1;
      const item: TimelineMessage = {
        kind: 'message',
        id: `msg-${role}-${seq}`,
        role,
        text: trimmed,
        ts,
        seq,
      };
      return {
        ...feed,
        timelineSeq: seq,
        timeline: trimTimeline([...feed.timeline, item]),
      };
    });
    syncThreadFeed(tid);
  },

  ingestEvent: (ev, threadId) => {
    if (ev.kind !== 'tool_result') return;
    const tool = (ev.detail?.tool as string | undefined) ?? '';
    const ingestKey = `${threadId ?? ''}:${ev.run_id ?? ''}:${ev.ts_ms}:${ev.agent}:${tool}`;
    if (get().lastIngestKey === ingestKey) return;
    set({ lastIngestKey: ingestKey });
    ingestToolResult(ev, threadId);
    syncThreadFeed(threadId);
  },

  ingestSearchResults: (results, threadId) => {
    if (!Array.isArray(results) || results.length === 0) return;
    const baseTs = Date.now();
    const cards = results
      .map((r, i) => normalizeListing(r, baseTs + i))
      .filter(Boolean) as CardData[];
    if (cards.length === 0) return;
    queuePendingListingCards(cards, threadId);
    syncThreadFeed(threadId);
  },

  commitPendingTimelineCards: (threadId) => {
    const tid = resolveThreadId(threadId);
    if (!tid) return;
    useThreadsStore.getState().patchFeed(tid, (feed) => {
      const base = patchListingMatchFeed(feed);
      const pending = base.pendingTimelineCards ?? [];
      if (pending.length === 0) return feed;

      const next = {
        ...base,
        timeline: [...base.timeline],
        pendingTimelineCards: [] as CardData[],
      };
      let seq = base.timelineSeq;
      const wallTs = Date.now();
      pending.forEach((card, i) => {
        const ts = wallTs + i;
        const stamped = { ...card, ts };
        seq += 1;
        next.timeline.push({
          kind: 'card',
          id: `card-${stamped.id}-${seq}`,
          card: stamped,
          ts,
          seq,
        });
      });
      return {
        ...next,
        timelineSeq: seq,
        timeline: trimTimeline(next.timeline),
      };
    });
    syncThreadFeed(tid);
  },

  saveListing: (listing) => {
    useThreadsStore.getState().patchActiveFeed((feed) => {
      const base = patchListingMatchFeed(feed);
      const savedListings = base.savedListings.some((row) => row.id === listing.id)
        ? base.savedListings.map((row) => (
          row.id === listing.id ? mergeListingDetails(row, listing) : row
        ))
        : [...base.savedListings, listing];
      const discardedListingIds = base.discardedListingIds.filter((id) => id !== listing.id);
      return { ...base, savedListings, discardedListingIds };
    });
    get().syncFromActiveThread();
  },

  discardListing: (listingId) => {
    useThreadsStore.getState().patchActiveFeed((feed) => {
      const base = patchListingMatchFeed(feed);
      const savedListings = base.savedListings.filter((row) => row.id !== listingId);
      const discardedListingIds = base.discardedListingIds.includes(listingId)
        ? base.discardedListingIds
        : [...base.discardedListingIds, listingId];
      return { ...base, savedListings, discardedListingIds };
    });
    get().syncFromActiveThread();
  },

  unsaveListing: (listingId) => {
    useThreadsStore.getState().patchActiveFeed((feed) => {
      const base = patchListingMatchFeed(feed);
      return {
        ...base,
        savedListings: base.savedListings.filter((row) => row.id !== listingId),
      };
    });
    get().syncFromActiveThread();
  },

  clearActive: () => {
    useThreadsStore.getState().replaceActiveFeed({
      timeline: [],
      timelineSeq: 0,
      listings: [],
      slots: [],
      audits: [],
      people: [],
      savedListings: [],
      discardedListingIds: [],
      pendingTimelineCards: [],
    });
    set({ lastIngestKey: '' });
    get().syncFromActiveThread();
  },

  getListingById: (id) => {
    for (const feed of allFeeds()) {
      const found = feed.listings.find((l) => l.id === id);
      if (found) return found;
    }
    return undefined;
  },
  getSlotById: (id) => {
    for (const feed of allFeeds()) {
      const found = feed.slots.find((s) => s.id === id);
      if (found) return found;
    }
    return undefined;
  },
  getAuditByDocumentId: (documentId) => {
    for (const feed of allFeeds()) {
      const found = feed.audits.find(
        (a) => a.document_id === documentId || a.id === documentId,
      );
      if (found) return found;
    }
    return undefined;
  },
  getPersonById: (id) => {
    for (const feed of allFeeds()) {
      const found = feed.people.find((p) => p.id === id);
      if (found) return found;
    }
    return undefined;
  },
}));

function queuePendingListingCards(cards: CardData[], threadId?: string) {
  if (cards.length === 0) return;
  const tid = resolveThreadId(threadId);
  if (!tid) return;
  const wallTs = Date.now();
  useThreadsStore.getState().patchFeed(tid, (state) => {
    const base = patchListingMatchFeed(state);
    const pending = [...(base.pendingTimelineCards ?? [])];
    const next = {
      ...base,
      listings: [...base.listings],
      slots: [...base.slots],
      audits: [...base.audits],
      people: [...base.people],
      pendingTimelineCards: pending,
    };
    cards.forEach((card, i) => {
      const ts = wallTs + i;
      const stamped = { ...card, ts };
      indexCard(stamped, next);
      if (card.kind === 'listing') {
        const alreadyPending = pending.some(
          (c) => c.kind === 'listing' && c.id === card.id,
        );
        const alreadyInFeed = base.timeline.some(
          (entry) => entry.kind === 'card'
            && entry.card.kind === 'listing'
            && entry.card.id === card.id,
        );
        if (alreadyPending || alreadyInFeed) return;
      }
      pending.push(stamped);
    });
    return {
      ...next,
      pendingTimelineCards: pending,
    };
  });
}

function ingestToolResult(ev: AgentTraceStep, threadId?: string) {
  const cards = extractCards(ev);
  if (cards.length === 0) return;
  queuePendingListingCards(cards, threadId);
}

function indexCard(
  card: CardData,
  next: {
    listings: ListingCardData[];
    slots: SlotCardData[];
    audits: AuditCardData[];
    people: PeopleCardData[];
  },
) {
  if (card.kind === 'listing') next.listings = pushListing(next.listings, card);
  else if (card.kind === 'slot') next.slots = pushCapped(next.slots, card);
  else if (card.kind === 'audit') next.audits = pushCapped(next.audits, card);
  else if (card.kind === 'people') next.people = pushCapped(next.people, card);
}

function pushListing(arr: ListingCardData[], item: ListingCardData): ListingCardData[] {
  const idx = arr.findIndex((x) => x.id === item.id);
  if (idx >= 0) {
    const merged = arr.slice();
    const prev = arr[idx]!;
    merged[idx] = mergeListingDetails(prev, item);
    return merged;
  }
  const merged = [...arr, item];
  if (merged.length > MAX_PER_TYPE) merged.splice(0, merged.length - MAX_PER_TYPE);
  return merged;
}

function pushCapped<T extends { id: string; ts: number }>(arr: T[], item: T): T[] {
  const idx = arr.findIndex((x) => x.id === item.id);
  if (idx >= 0) {
    const merged = arr.slice();
    merged[idx] = item;
    return merged;
  }
  const merged = [...arr, item];
  if (merged.length > MAX_PER_TYPE) merged.splice(0, merged.length - MAX_PER_TYPE);
  return merged;
}

function trimTimeline(items: TimelineItem[]): TimelineItem[] {
  const ordered = sortTimeline(items);
  if (ordered.length <= MAX_TIMELINE) return ordered;
  return ordered.slice(ordered.length - MAX_TIMELINE);
}

/** Latest committed assistant bubble in this thread (ignores in-flight streaming). */
export function lastAssistantMessageText(items: TimelineItem[]): string | null {
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const entry = items[i];
    if (entry?.kind === 'message' && entry.role === 'assistant') {
      return entry.text.trim();
    }
  }
  return null;
}

/** Hide streaming caption once the same text is committed to the timeline. */
export function shouldShowStreamingReply(
  items: TimelineItem[],
  partial: string,
): boolean {
  const text = partial.trim();
  if (!text) return false;
  const committed = lastAssistantMessageText(items);
  if (!committed) return true;
  return committed !== text;
}

/** Chronological feed order — insertion `seq`, not orchestrator `ts_ms`. */
export function sortTimeline(items: TimelineItem[]): TimelineItem[] {
  return [...items].sort((a, b) => {
    const as = a.seq ?? 0;
    const bs = b.seq ?? 0;
    if (as !== bs) return as - bs;
    return a.ts - b.ts;
  });
}

export function formatTimelineTs(ts: number): string {
  try {
    return new Intl.DateTimeFormat('es-MX', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(new Date(ts));
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// Extraction
// ---------------------------------------------------------------------------
export function extractCards(ev: AgentTraceStep): CardData[] {
  if (ev.kind !== 'tool_result') return [];
  const tool = (ev.detail?.tool as string | undefined) ?? '';
  const result = (ev.detail?.result as unknown) ?? null;
  if (!result || typeof result !== 'object') return [];

  const baseTs = ev.ts_ms > 0 ? ev.ts_ms : Date.now();

  if (tool === 'search_listings') {
    const items = (result as { results?: unknown[] }).results ?? [];
    return items.map((r, i) => normalizeListing(r, baseTs + i)).filter(Boolean) as CardData[];
  }

  if (ev.agent === 'locator_agent') {
    if (tool === 'get_listing') {
      return [normalizeListing(result, baseTs)].filter(Boolean) as CardData[];
    }
    if (tool === 'compare_listings') {
      const items = (result as { items?: unknown[] }).items ?? [];
      return items.map((r, i) => normalizeListing(r, baseTs + i)).filter(Boolean) as CardData[];
    }
    if (tool === 'find_buyers') {
      const items = (result as { results?: unknown[] }).results ?? [];
      return items.map((r, i) => normalizePerson(r, 'buyer', baseTs + i)).filter(Boolean) as CardData[];
    }
    if (tool === 'find_collaborator_agents') {
      const items = (result as { results?: unknown[] }).results ?? [];
      return items.map((r, i) => normalizePerson(r, 'collaborator', baseTs + i)).filter(Boolean) as CardData[];
    }
    if (tool === 'find_brokers') {
      const items = (result as { results?: unknown[] }).results ?? [];
      return items.map((r, i) => normalizePerson(r, 'broker', baseTs + i)).filter(Boolean) as CardData[];
    }
  }

  if (ev.agent === 'realestate_agent' && /slot|schedule|visit/i.test(tool)) {
    const slots =
      (result as { slots?: unknown[] }).slots
      ?? [(result as object)];
    return slots.map((r, i) => normalizeSlot(r, baseTs + i)).filter(Boolean) as CardData[];
  }

  if (ev.agent === 'audit_agent') {
    if (tool === 'rpp_lookup') return [normalizeRpp(result, baseTs)].filter(Boolean) as CardData[];
    if (tool === 'catastro_lookup') return [normalizeCatastro(result, baseTs)].filter(Boolean) as CardData[];
    if (tool === 'inegi_zone_stats') return [normalizeInegi(result, baseTs)].filter(Boolean) as CardData[];
    if (tool === 'sat_rfc_check') return [normalizeSat(result, baseTs)].filter(Boolean) as CardData[];
    if (/verify|title|catastro|rpp/i.test(tool)) {
      return [normalizeAudit(result, baseTs)].filter(Boolean) as CardData[];
    }
  }

  return [];
}

function normalizeListing(r: unknown, ts: number): ListingCardData | null {
  const parsed = parseListingRecord(r, ts);
  if (!parsed) return null;
  // Card feed shows a short feature list; detail screen loads the full record.
  if (parsed.features && parsed.features.length > 8) {
    return { ...parsed, features: parsed.features.slice(0, 8) };
  }
  return parsed;
}

function normalizePerson(
  r: unknown,
  personKind: PeopleCardData['person_kind'],
  ts: number,
): PeopleCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const id = o.id as string | undefined;
  const name = o.name as string | undefined;
  if (!id || !name) return null;
  const neighborhoods = Array.isArray(o.neighborhoods)
    ? (o.neighborhoods as string[]).join(', ')
    : '';
  const location = [neighborhoods, o.city, o.state].filter(Boolean).join(' · ');
  const tags: string[] = [];
  if (personKind === 'buyer') {
    if (typeof o.budget_mxn === 'number') tags.push(`$${Math.round(o.budget_mxn / 1e6)}M`);
    if (typeof o.stage === 'string') tags.push(o.stage);
    if (Array.isArray(o.property_types)) tags.push(...(o.property_types as string[]).slice(0, 2));
  } else if (personKind === 'collaborator') {
    if (Array.isArray(o.languages)) tags.push(...(o.languages as string[]));
    if (Array.isArray(o.specialties)) tags.push(...(o.specialties as string[]).slice(0, 2));
    if (typeof o.availability === 'string') tags.push(o.availability);
  } else {
    if (typeof o.firm === 'string') tags.push(o.firm);
    if (typeof o.license === 'string') tags.push(o.license);
    if (Array.isArray(o.specialties)) tags.push(...(o.specialties as string[]).slice(0, 2));
  }
  const score = typeof o.score === 'number'
    ? o.score
    : typeof o.lead_score === 'number'
      ? o.lead_score
      : typeof o.rating === 'number'
        ? o.rating
        : undefined;
  return {
    kind: 'people',
    person_kind: personKind,
    id: String(id),
    name,
    subtitle: personKind === 'broker' && typeof o.firm === 'string' ? o.firm : undefined,
    location: location || String(o.state ?? ''),
    tags,
    score,
    source_agent: 'locator_agent',
    ts,
  };
}

function normalizeSlot(r: unknown, ts: number): SlotCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const id = (o.id ?? o.slot_id) as string | undefined;
  const starts = (o.starts_at_iso ?? o.starts_at) as string | undefined;
  const ends = (o.ends_at_iso ?? o.ends_at) as string | undefined;
  if (!id || !starts || !ends) return null;
  return {
    kind: 'slot',
    id: String(id),
    listing_id: typeof o.listing_id === 'string' ? (o.listing_id as string) : undefined,
    starts_at_iso: starts,
    ends_at_iso: ends,
    agent_name: (o.agent_name ?? 'Asesor') as string,
    status: ((o.status as SlotCardData['status']) ?? 'proposed'),
    source_agent: 'realestate_agent',
    ts,
  };
}

function registryIsMock(o: Record<string, unknown>): boolean {
  const src = String(o.source ?? '');
  if (src === 'live' || src === 'live_partial') return false;
  if (src === 'mock') return true;
  return String(o.verification_token ?? '').includes('MOCK');
}

function normalizeRpp(r: unknown, ts: number): AuditCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const folio = String(o.folio_real ?? o.document_id ?? o.verification_token ?? '');
  if (!folio) return null;
  const enc = Array.isArray(o.encumbrances) ? o.encumbrances as Array<Record<string, unknown>> : [];
  const findings: AuditCardData['findings'] = [
    { label: `Propietario: ${o.registered_owner ?? '—'}`, level: 'ok' },
    { label: `Estatus: ${o.status ?? '—'}`, level: enc.length ? 'warn' : 'ok' },
  ];
  enc.forEach((e) => {
    findings.push({
      label: `Gravamen ${e.type}: ${e.creditor}`,
      level: 'warn',
    });
  });
  return {
    kind: 'audit',
    id: `rpp-${folio}`,
    document_id: folio,
    topic: 'title',
    headline: `RPP · ${folio}`,
    score: enc.length ? 0.45 : 0.95,
    findings,
    source_agent: 'audit_agent',
    ts,
    mock: registryIsMock(o),
  };
}

function normalizeCatastro(r: unknown, ts: number): AuditCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const clave = String(o.clave_catastral ?? o.verification_token ?? '');
  if (!clave) return null;
  const alCorriente = Boolean(o.al_corriente);
  return {
    kind: 'audit',
    id: `cat-${clave}`,
    document_id: clave,
    topic: 'tax',
    headline: `Catastro · ${clave}`,
    score: alCorriente ? 0.9 : 0.5,
    findings: [
      { label: `Uso de suelo: ${o.uso_de_suelo ?? '—'}`, level: 'ok' },
      {
        label: alCorriente
          ? `Predial al corriente (${o.ultimo_pago_anio})`
          : `Predial pendiente — último pago ${o.ultimo_pago_anio}`,
        level: alCorriente ? 'ok' : 'warn',
      },
      {
        label: `Valor catastral: $${Number(o.valor_catastral_mxn ?? 0).toLocaleString('es-MX')} MXN`,
        level: 'ok',
      },
    ],
    source_agent: 'audit_agent',
    ts,
    mock: registryIsMock(o),
  };
}

function normalizeInegi(r: unknown, ts: number): AuditCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const ageb = String(o.ageb_id ?? 'AGEB');
  return {
    kind: 'audit',
    id: `inegi-${ageb}`,
    document_id: ageb,
    topic: 'inegi',
    headline: `INEGI · ${ageb}`,
    score: 0.85,
    findings: [
      { label: `Población AGEB: ${o.population ?? '—'}`, level: 'ok' },
      {
        label: o.median_household_income_mxn != null
          ? `Ingreso medio: $${Number(o.median_household_income_mxn).toLocaleString('es-MX')} MXN`
          : `Unidades económicas 500 m: ${o.economic_units_500m ?? '—'}`,
        level: 'ok',
      },
      { label: `Escuelas ≤1 km: ${o.schools_within_1km ?? '—'}`, level: 'ok' },
      {
        label: `Índice delictivo 2025: ${o.crime_index_2025 ?? '—'}/5`,
        level: Number(o.crime_index_2025) > 3.5 ? 'warn' : 'ok',
      },
    ],
    source_agent: 'audit_agent',
    ts,
    mock: registryIsMock(o),
  };
}

function normalizeSat(r: unknown, ts: number): AuditCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const rfc = String(o.rfc ?? '');
  if (!rfc) return null;
  const blocked = Boolean(o.lista_69 || o.lista_efos);
  return {
    kind: 'audit',
    id: `sat-${rfc}`,
    document_id: rfc,
    topic: 'sat',
    headline: `SAT · ${rfc}`,
    score: blocked ? 0.2 : 0.95,
    findings: [
      { label: `Estatus: ${o.estatus_padron ?? '—'}`, level: blocked ? 'block' : 'ok' },
      { label: `Régimen: ${o.regimen_fiscal ?? '—'}`, level: 'ok' },
      ...(o.lista_69 ? [{ label: 'Lista 69-B', level: 'block' as const }] : []),
      ...(o.lista_efos ? [{ label: 'Lista EFOS', level: 'block' as const }] : []),
    ],
    source_agent: 'audit_agent',
    ts,
    mock: registryIsMock(o),
  };
}

function normalizeAudit(r: unknown, ts: number): AuditCardData | null {
  if (!r || typeof r !== 'object') return null;
  const o = r as Record<string, unknown>;
  const documentId = (o.document_id ?? o.folio_real ?? o.clave_catastral) as string | undefined;
  if (!documentId) return null;
  const findings = Array.isArray(o.findings)
    ? (o.findings as Array<{ label?: string; level?: string }>)
      .map((f): { label: string; level: 'ok' | 'warn' | 'block' } => ({
        label: f.label ?? '',
        level: f.level === 'warn' || f.level === 'block' ? f.level : 'ok',
      }))
    : [];
  return {
    kind: 'audit',
    id: String(documentId),
    document_id: String(documentId),
    listing_id: typeof o.listing_id === 'string' ? (o.listing_id as string) : undefined,
    topic: ((o.topic as AuditCardData['topic']) ?? 'title'),
    headline: (o.headline ?? o.status ?? 'Verificación') as string,
    score: Number(o.score ?? 0),
    findings,
    source_agent: 'audit_agent',
    ts,
  };
}
