/**
 * Listing match review — swipe right to save, left to discard (per thread).
 */
import type { ListingCardData, TimelineItem } from '@/state/cardsStore';
import type { ThreadFeedSnapshot } from '@/state/threadsStore';

export function normalizeListingMatchFeed(
  feed: Partial<ThreadFeedSnapshot> | undefined,
): Pick<ThreadFeedSnapshot, 'savedListings' | 'discardedListingIds'> {
  return {
    savedListings: Array.isArray(feed?.savedListings) ? feed.savedListings : [],
    discardedListingIds: Array.isArray(feed?.discardedListingIds)
      ? feed.discardedListingIds
      : [],
  };
}

export function savedListingIds(saved: ListingCardData[]): Set<string> {
  return new Set(saved.map((l) => l.id));
}

export function discardedListingIdSet(ids: string[]): Set<string> {
  return new Set(ids);
}

/** Timeline entries for listing cards still pending review. */
export function filterPendingListingTimeline(
  timeline: TimelineItem[],
  savedIds: Set<string>,
  discardedIds: Set<string>,
): TimelineItem[] {
  return timeline.filter((entry) => {
    if (entry.kind !== 'card' || entry.card.kind !== 'listing') return true;
    const id = entry.card.id;
    return !savedIds.has(id) && !discardedIds.has(id);
  });
}

export function countPendingListingCards(
  timeline: TimelineItem[],
  savedIds: Set<string>,
  discardedIds: Set<string>,
): number {
  return timeline.filter(
    (entry) => entry.kind === 'card'
      && entry.card.kind === 'listing'
      && !savedIds.has(entry.card.id)
      && !discardedIds.has(entry.card.id),
  ).length;
}
