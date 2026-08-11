/**
 * Client-side geo helpers — Phase 6 (map pin sorting fallback).
 */
export function haversineKm(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
): number {
  const r = 6371;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dlat = ((lat2 - lat1) * Math.PI) / 180;
  const dlng = ((lng2 - lng1) * Math.PI) / 180;
  const a =
    Math.sin(dlat / 2) ** 2
    + Math.cos(p1) * Math.cos(p2) * Math.sin(dlng / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(Math.min(1, a)));
}

export function sortByDistance<T extends { lat?: number; lng?: number }>(
  items: T[],
  originLat: number,
  originLng: number,
): T[] {
  return [...items].sort((a, b) => {
    const da =
      a.lat != null && a.lng != null
        ? haversineKm(originLat, originLng, a.lat, a.lng)
        : Number.POSITIVE_INFINITY;
    const db =
      b.lat != null && b.lng != null
        ? haversineKm(originLat, originLng, b.lat, b.lng)
        : Number.POSITIVE_INFINITY;
    return da - db;
  });
}
