/**
 * Phase 6 feature flags — maps, tours, calendar.
 * Screens stay hidden until EXPO_PUBLIC_PHASE6_MAPS=1 at build time.
 */
export const phase6 = {
  mapsEnabled: process.env.EXPO_PUBLIC_PHASE6_MAPS === '1',
  routingBaseUrl:
    (process.env.EXPO_PUBLIC_ROUTING_URL as string | undefined)
    ?? 'http://localhost:8008',
  listingsBaseUrl:
    (process.env.EXPO_PUBLIC_LISTINGS_URL as string | undefined)
    ?? 'http://localhost:8002',
} as const;
