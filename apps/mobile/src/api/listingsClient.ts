/**
 * Direct read access to the listings service for detail drilldown.
 */
import { parseListingRecord } from '@/utils/listingFormat';
import type { ListingCardData } from '@/state/cardsStore';
import { phase6 } from '@/config/phase6';

const LISTINGS_BASE_URL =
  (process.env.EXPO_PUBLIC_LISTINGS_URL as string | undefined)
  ?? phase6.listingsBaseUrl;

export async function fetchListingById(id: string): Promise<ListingCardData | null> {
  const url = `${LISTINGS_BASE_URL.replace(/\/$/, '')}/listings/${encodeURIComponent(id)}`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) return null;
    const data: unknown = await resp.json();
    return parseListingRecord(data, Date.now());
  } catch {
    return null;
  }
}

export async function fetchListingsNearby(
  lat: number,
  lng: number,
  opts?: { radius_km?: number; state?: string; limit?: number },
): Promise<ListingCardData[]> {
  const params = new URLSearchParams({
    lat: String(lat),
    lng: String(lng),
    radius_km: String(opts?.radius_km ?? 5),
    limit: String(opts?.limit ?? 30),
  });
  if (opts?.state) params.set('state', opts.state);
  const url = `${LISTINGS_BASE_URL.replace(/\/$/, '')}/listings/nearby?${params}`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) return [];
    const data: unknown = await resp.json();
    if (!Array.isArray(data)) return [];
    return data
      .map((row, i) => parseListingRecord(row, Date.now() + i))
      .filter(Boolean) as ListingCardData[];
  } catch {
    return [];
  }
}
