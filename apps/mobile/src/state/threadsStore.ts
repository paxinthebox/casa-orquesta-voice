/**
 * Client threads — one workspace per buyer/seller/deal (Phase A).
 *
 * threadId is the mobile key; conversationId (c-…) is the orchestrator id.
 * voiceSessionId (S-…) is the gateway WS session, one per thread when possible.
 */
import { create } from 'zustand';

import { getAppStorage, STORAGE_KEYS } from '@/storage/mmkv';
import type {
  AuditCardData,
  CardData,
  ListingCardData,
  PeopleCardData,
  SlotCardData,
  TimelineItem,
} from '@/state/cardsStore';
import {
  EMPTY_CLIENT_PROFILE,
  normalizeClientProfile,
  type ClientProfileDraft,
} from '@/utils/clientProfile';
import { normalizeListingMatchFeed } from '@/utils/listingMatch';

export type ClientRole = 'buyer' | 'seller';

export interface ThreadMeta {
  id: string;
  conversationId: string;
  label: string;
  clientRole: ClientRole;
  voiceSessionId: string | null;
  welcomeSent: boolean;
  createdAt: number;
  updatedAt: number;
}

export interface ThreadFeedSnapshot {
  timeline: TimelineItem[];
  timelineSeq: number;
  listings: ListingCardData[];
  slots: SlotCardData[];
  audits: AuditCardData[];
  people: PeopleCardData[];
  savedListings: ListingCardData[];
  discardedListingIds: string[];
  /** Cards indexed on tool_result; appended to timeline after the assistant reply. */
  pendingTimelineCards: CardData[];
}

export interface ThreadsPersisted {
  threads: ThreadMeta[];
  activeThreadId: string;
  feeds: Record<string, ThreadFeedSnapshot>;
  clientProfiles?: Record<string, ClientProfileDraft>;
}

function emptyFeed(): ThreadFeedSnapshot {
  return {
    timeline: [],
    timelineSeq: 0,
    listings: [],
    slots: [],
    audits: [],
    people: [],
    savedListings: [],
    discardedListingIds: [],
    pendingTimelineCards: [],
  };
}

function newId(prefix: string): string {
  const hex = Math.random().toString(16).slice(2, 14);
  return `${prefix}-${hex}`;
}

function defaultLabel(count: number): string {
  return `Cliente ${count}`;
}

/** Zustand selectors — stable references when profile is empty. */
export function selectActiveThread(state: ThreadsStore): ThreadMeta | null {
  if (!state.activeThreadId) return null;
  return state.threads.find((t) => t.id === state.activeThreadId) ?? null;
}

/** Raw stored profile — stable reference for Zustand subscriptions. */
export function selectActiveClientProfileRaw(
  state: ThreadsStore,
): ClientProfileDraft | undefined {
  const id = state.activeThreadId;
  if (!id) return undefined;
  return state.clientProfiles[id];
}

/** Normalized profile for one-off reads (not for useStore selectors). */
export function selectActiveClientProfile(state: ThreadsStore): ClientProfileDraft {
  const raw = selectActiveClientProfileRaw(state);
  if (!raw) return EMPTY_CLIENT_PROFILE as ClientProfileDraft;
  return normalizeClientProfile(raw);
}

interface ThreadsStore {
  threads: ThreadMeta[];
  activeThreadId: string | null;
  feeds: Record<string, ThreadFeedSnapshot>;
  clientProfiles: Record<string, ClientProfileDraft>;
  hydrated: boolean;

  hydrate: () => void;
  persist: () => void;
  createThread: (opts?: { label?: string; clientRole?: ClientRole }) => string;
  switchThread: (threadId: string) => void;
  deleteThread: (threadId: string) => boolean;
  renameActiveThread: (label: string) => void;
  setActiveThreadRole: (role: ClientRole) => void;
  getActiveThread: () => ThreadMeta | null;
  getActiveFeed: () => ThreadFeedSnapshot;
  replaceActiveFeed: (feed: ThreadFeedSnapshot) => void;
  patchActiveFeed: (
    patch: Partial<ThreadFeedSnapshot> | ((prev: ThreadFeedSnapshot) => ThreadFeedSnapshot),
  ) => void;
  /** Patch a specific thread feed (used when a turn started on that thread). */
  patchFeed: (
    threadId: string,
    patch: Partial<ThreadFeedSnapshot> | ((prev: ThreadFeedSnapshot) => ThreadFeedSnapshot),
  ) => void;
  /** Guarantee an active client thread; creates one if the inbox is empty. */
  ensureActiveThread: (opts?: { label?: string; clientRole?: ClientRole }) => string;
  setVoiceSessionId: (threadId: string, voiceSessionId: string) => void;
  markWelcomeSent: (threadId: string) => void;
  getActiveClientProfile: () => ClientProfileDraft;
  getClientProfile: (threadId: string) => ClientProfileDraft;
  setActiveClientProfile: (profile: ClientProfileDraft) => void;
}

export const useThreadsStore = create<ThreadsStore>((set, get) => ({
  threads: [],
  activeThreadId: null,
  feeds: {},
  clientProfiles: {},
  hydrated: false,

  hydrate: () => {
    if (get().hydrated) return;
    try {
      const raw = getAppStorage().getString(STORAGE_KEYS.threads);
      if (raw) {
        const parsed = JSON.parse(raw) as ThreadsPersisted;
        if (Array.isArray(parsed?.threads)) {
          set({
            threads: parsed.threads,
            activeThreadId: parsed.activeThreadId || null,
            feeds: parsed.feeds ?? {},
            clientProfiles: parsed.clientProfiles ?? {},
            hydrated: true,
          });
          return;
        }
      }
    } catch {
      /* fall through to empty workspace */
    }
    set({
      threads: [],
      activeThreadId: null,
      feeds: {},
      clientProfiles: {},
      hydrated: true,
    });
  },

  persist: () => {
    const { threads, activeThreadId, feeds, clientProfiles } = get();
    const payload: ThreadsPersisted = {
      threads,
      activeThreadId: activeThreadId ?? '',
      feeds,
      clientProfiles,
    };
    getAppStorage().set(STORAGE_KEYS.threads, JSON.stringify(payload));
  },

  createThread: (opts) => {
    const state = get();
    const id = newId('t');
    const conversationId = newId('c');
    const thread: ThreadMeta = {
      id,
      conversationId,
      label: opts?.label ?? defaultLabel(state.threads.length + 1),
      clientRole: opts?.clientRole ?? 'buyer',
      voiceSessionId: null,
      welcomeSent: false,
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    set({
      threads: [...state.threads, thread],
      activeThreadId: id,
      feeds: { ...state.feeds, [id]: emptyFeed() },
    });
    get().persist();
    return id;
  },

  switchThread: (threadId) => {
    if (!get().threads.some((t) => t.id === threadId)) return;
    set({ activeThreadId: threadId });
    get().persist();
  },

  deleteThread: (threadId) => {
    const state = get();
    if (!state.threads.some((t) => t.id === threadId)) return false;
    const nextThreads = state.threads.filter((t) => t.id !== threadId);
    const nextFeeds = { ...state.feeds };
    delete nextFeeds[threadId];
    const nextProfiles = { ...state.clientProfiles };
    delete nextProfiles[threadId];
    let nextActive = state.activeThreadId;
    if (nextActive === threadId) {
      nextActive = nextThreads[0]?.id ?? null;
    }
    set({
      threads: nextThreads,
      feeds: nextFeeds,
      clientProfiles: nextProfiles,
      activeThreadId: nextActive,
    });
    get().persist();
    return true;
  },

  renameActiveThread: (label) => {
    const trimmed = label.trim();
    if (!trimmed) return;
    const activeId = get().activeThreadId;
    if (!activeId) return;
    set({
      threads: get().threads.map((t) =>
        t.id === activeId ? { ...t, label: trimmed, updatedAt: Date.now() } : t,
      ),
    });
    get().persist();
  },

  setActiveThreadRole: (role) => {
    const activeId = get().activeThreadId;
    if (!activeId) return;
    set({
      threads: get().threads.map((t) =>
        t.id === activeId ? { ...t, clientRole: role, updatedAt: Date.now() } : t,
      ),
    });
    get().persist();
  },

  getActiveThread: () => {
    const { activeThreadId, threads } = get();
    return threads.find((t) => t.id === activeThreadId) ?? null;
  },

  getActiveFeed: () => {
    const activeId = get().activeThreadId;
    if (!activeId) return emptyFeed();
    const raw = get().feeds[activeId] ?? emptyFeed();
    return {
      ...raw,
      ...normalizeListingMatchFeed(raw),
      pendingTimelineCards: raw.pendingTimelineCards ?? [],
    };
  },

  replaceActiveFeed: (feed) => {
    const activeId = get().activeThreadId;
    if (!activeId) return;
    set({
      feeds: { ...get().feeds, [activeId]: feed },
      threads: get().threads.map((t) =>
        t.id === activeId ? { ...t, updatedAt: Date.now() } : t,
      ),
    });
    get().persist();
  },

  patchActiveFeed: (patch) => {
    const activeId = get().activeThreadId;
    if (!activeId) return;
    get().patchFeed(activeId, patch);
  },

  patchFeed: (threadId, patch) => {
    if (!get().threads.some((t) => t.id === threadId)) return;
    const prev = get().feeds[threadId] ?? emptyFeed();
    const next = typeof patch === 'function' ? patch(prev) : { ...prev, ...patch };
    set({
      feeds: { ...get().feeds, [threadId]: next },
      threads: get().threads.map((t) =>
        t.id === threadId ? { ...t, updatedAt: Date.now() } : t,
      ),
    });
    get().persist();
  },

  ensureActiveThread: (opts) => {
    const existing = get().activeThreadId;
    if (existing && get().threads.some((t) => t.id === existing)) return existing;
    if (get().threads.length > 0) {
      const id = get().threads[0]!.id;
      set({ activeThreadId: id });
      get().persist();
      return id;
    }
    return get().createThread(opts);
  },

  setVoiceSessionId: (threadId, voiceSessionId) => {
    set({
      threads: get().threads.map((t) =>
        t.id === threadId
          ? { ...t, voiceSessionId, updatedAt: Date.now() }
          : t,
      ),
    });
    get().persist();
  },

  markWelcomeSent: (threadId) => {
    set({
      threads: get().threads.map((t) =>
        t.id === threadId ? { ...t, welcomeSent: true, updatedAt: Date.now() } : t,
      ),
    });
    get().persist();
  },

  getActiveClientProfile: () => {
    return selectActiveClientProfile(get());
  },

  getClientProfile: (threadId) => {
    const raw = get().clientProfiles[threadId];
    if (!raw) return EMPTY_CLIENT_PROFILE as ClientProfileDraft;
    return normalizeClientProfile(raw);
  },

  setActiveClientProfile: (profile) => {
    const activeId = get().activeThreadId;
    if (!activeId) return;
    set({
      clientProfiles: { ...get().clientProfiles, [activeId]: profile },
    });
    get().persist();
  },
}));
