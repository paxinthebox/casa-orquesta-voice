/**
 * SessionProvider — global app state (Zustand-backed).
 *
 * Phase 3.1: just the skeleton — onboarding-complete + consent-given flags,
 * tenant/user identifiers, focus pins (the IDs the user tapped to narrow
 * the next voice turn). Identity wiring lands in Phase 4 (Auth0 phone OTP).
 */
import React from 'react';
import { StyleSheet, View } from 'react-native';
import { create } from 'zustand';

export type SessionState = {
  onboardingComplete: boolean;
  consentGiven: boolean;
  tenantId: string | null;
  userId: string | null;
  authToken: string | null;
  locale: 'es-MX' | 'en-US';
  // Focus pins narrow the next voice turn — set by tapping cards.
  focusListingId: string | null;
  focusDocumentId: string | null;
  focusPersonId: string | null;
  focusPersonKind: 'buyer' | 'collaborator' | 'broker' | null;
  focusPersonName: string | null;

  // Setters
  setOnboardingComplete: (v: boolean) => void;
  setConsentGiven: (v: boolean) => void;
  setIdentity: (i: {
    tenantId: string | null;
    userId: string | null;
    authToken: string | null;
  }) => void;
  setLocale: (l: 'es-MX' | 'en-US') => void;
  setFocusListing: (id: string | null) => void;
  setFocusDocument: (id: string | null) => void;
  setFocusPerson: (
    id: string | null,
    meta?: { kind?: 'buyer' | 'collaborator' | 'broker'; name?: string },
  ) => void;
  reset: () => void;
};

const INITIAL: Omit<SessionState,
  | 'setOnboardingComplete'
  | 'setConsentGiven'
  | 'setIdentity'
  | 'setLocale'
  | 'setFocusListing'
  | 'setFocusDocument'
  | 'setFocusPerson'
  | 'reset'
> = {
  onboardingComplete: false,
  consentGiven: false,
  tenantId: null,
  userId: null,
  authToken: null,
  locale: 'es-MX',
  focusListingId: null,
  focusDocumentId: null,
  focusPersonId: null,
  focusPersonKind: null,
  focusPersonName: null,
};

export const useSession = create<SessionState>((set) => ({
  ...INITIAL,
  setOnboardingComplete: (v) => set({ onboardingComplete: v }),
  setConsentGiven: (v) => set({ consentGiven: v }),
  setIdentity: (i) => set(i),
  setLocale: (l) => set({ locale: l }),
  setFocusListing: (id) => set({ focusListingId: id }),
  setFocusDocument: (id) => set({ focusDocumentId: id }),
  setFocusPerson: (id, meta) => set({
    focusPersonId: id,
    focusPersonKind: id ? (meta?.kind ?? null) : null,
    focusPersonName: id ? (meta?.name ?? null) : null,
  }),
  reset: () => set(INITIAL),
}));

/**
 * SessionProvider is the React component App.tsx mounts. It doesn't have
 * to do anything yet (Zustand stores are module-level), but keeping it as
 * a component leaves room for a hydration step (MMKV restore) in P3.2.
 */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  return <View style={styles.root}>{children}</View>;
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
