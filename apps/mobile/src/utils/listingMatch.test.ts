import type { ListingCardData, TimelineItem } from '@/state/cardsStore';
import {
  countPendingListingCards,
  filterPendingListingTimeline,
  normalizeListingMatchFeed,
  savedListingIds,
} from './listingMatch';

function listing(id: string): ListingCardData {
  return {
    kind: 'listing',
    id,
    title: `Listing ${id}`,
    price_mxn: 1_000_000,
    zone: 'Roma',
    city: 'Ciudad de México',
    state: 'CDMX',
    source_agent: 'locator_agent',
    ts: 1,
  };
}

function cardEntry(id: string): TimelineItem {
  return {
    kind: 'card',
    id: `card-${id}`,
    card: listing(id),
    ts: 1,
    seq: 1,
  };
}

describe('listingMatch', () => {
  it('normalizes legacy feeds without match fields', () => {
    expect(normalizeListingMatchFeed({})).toEqual({
      savedListings: [],
      discardedListingIds: [],
    });
  });

  it('hides saved and discarded listing cards from the pending feed', () => {
    const timeline = [cardEntry('a'), cardEntry('b'), cardEntry('c')];
    const saved = savedListingIds([listing('b')]);
    const discarded = new Set(['c']);
    const pending = filterPendingListingTimeline(timeline, saved, discarded);
    expect(pending.map((entry) => entry.kind === 'card' ? entry.card.id : entry.id)).toEqual(['a']);
    expect(countPendingListingCards(timeline, saved, discarded)).toBe(1);
  });
});
