/**
 * Casa·Orquesta · Voice — design system.
 *
 * Colors mirror the MVP buyer.html palette so the mobile app feels
 * continuous with the web prototype reviewers have already seen.
 * Per-agent accent colors are deliberately the same hue family as on
 * the web (locator green, audit purple, realestate gold) — they're how
 * users mentally tag which sub-agent did what.
 */

export const colors = {
  // --- Surfaces ---
  navy: '#0B1426',         // deep background
  navyEl1: '#142037',      // elevated card / sheet
  navyEl2: '#1B2A47',      // tap-active surface
  ink: '#0E1B2E',          // headings on light surfaces
  bone: '#F6F1E7',         // warm off-white (MVP brand)
  bonePale: '#FBF8F1',
  hairline: 'rgba(255,255,255,0.08)',
  hairlineInk: 'rgba(14,27,46,0.10)',

  // --- Brand ---
  gold: '#D4A24C',         // primary accent (Casa·Orquesta gold)
  goldDim: '#A37E33',
  goldFaint: 'rgba(212,162,76,0.16)',

  // --- Per-agent accents (must match web buyer.html) ---
  agentRealestate: '#D4A24C',  // gold
  agentLocator: '#3FB58A',     // green
  agentAudit: '#9B7CB5',       // purple

  // --- Semantic ---
  textPrimary: '#F6F1E7',
  textSecondary: 'rgba(246,241,231,0.75)',
  textMuted: 'rgba(246,241,231,0.55)',
  textOnLight: '#0E1B2E',
  textOnLightMuted: 'rgba(14,27,46,0.62)',

  success: '#3FB58A',
  warning: '#E6B84A',
  danger: '#D8553F',
  info: '#6CA8DE',

  // --- Mic states (used by P3.3 MicButton) ---
  micIdle: '#D4A24C',
  micListening: '#3FB58A',
  micSpeaking: '#6CA8DE',
  micError: '#D8553F',
} as const;

export const spacing = {
  xxs: 2,
  xs: 4,
  s: 8,
  m: 12,
  l: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

export const radii = {
  xs: 4,
  s: 8,
  m: 12,
  l: 16,
  xl: 24,
  pill: 999,
} as const;

export const typography = {
  display: { fontSize: 34, lineHeight: 40, fontWeight: '700' as const },
  h1:      { fontSize: 28, lineHeight: 34, fontWeight: '700' as const },
  h2:      { fontSize: 22, lineHeight: 28, fontWeight: '600' as const },
  h3:      { fontSize: 18, lineHeight: 24, fontWeight: '600' as const },
  body:    { fontSize: 16, lineHeight: 22, fontWeight: '400' as const },
  bodyBold:{ fontSize: 16, lineHeight: 22, fontWeight: '600' as const },
  caption: { fontSize: 13, lineHeight: 18, fontWeight: '400' as const },
  micro:   { fontSize: 11, lineHeight: 14, fontWeight: '500' as const },
} as const;

export const shadow = {
  card: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.18,
    shadowRadius: 12,
    elevation: 3,
  },
  fab: {
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.28,
    shadowRadius: 18,
    elevation: 8,
  },
} as const;

export const motion = {
  fast: 150,
  normal: 250,
  slow: 400,
} as const;

export const theme = {
  colors,
  spacing,
  radii,
  typography,
  shadow,
  motion,
} as const;

export type Theme = typeof theme;
export default theme;
