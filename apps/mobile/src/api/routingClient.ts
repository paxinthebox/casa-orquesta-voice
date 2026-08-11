/**
 * Routing service client — Phase 6 prep (haversine optimize).
 */
import { phase6 } from '@/config/phase6';

export interface TourOrigin {
  type: 'user_location' | 'listing' | 'address';
  lat: number;
  lng: number;
  label?: string;
}

export interface RouteLeg {
  from_id: string;
  to_id: string;
  distance_m: number;
  duration_s: number;
  provider?: string;
}

export interface OptimizeTourResponse {
  provider: string;
  origin: TourOrigin;
  ordered_listing_ids: string[];
  missing_listing_ids: string[];
  legs: RouteLeg[];
  total_distance_m: number;
  total_drive_s: number;
  total_dwell_s: number;
  estimated_tour_s: number;
  dwell_minutes_per_stop: number;
}

export async function optimizeTourRoute(payload: {
  origin: TourOrigin;
  listing_ids: string[];
  return_to_origin?: boolean;
  dwell_minutes?: number;
}): Promise<OptimizeTourResponse | null> {
  const base = phase6.routingBaseUrl.replace(/\/$/, '');
  try {
    const resp = await fetch(`${base}/route/optimize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) return null;
    return (await resp.json()) as OptimizeTourResponse;
  } catch {
    return null;
  }
}

export async function fetchRoutingHealth(): Promise<boolean> {
  const base = phase6.routingBaseUrl.replace(/\/$/, '');
  try {
    const resp = await fetch(`${base}/health`);
    return resp.ok;
  } catch {
    return false;
  }
}
