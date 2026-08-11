/**
 * i18n bootstrap. ES-MX is primary; we fall back to it on missing keys.
 *
 * Lazy-initialized — first call to `getI18n()` constructs the instance so
 * unit tests can mock the locale by injecting their own dictionary before
 * any screen renders.
 */
import i18n, { type i18n as I18nInstance } from 'i18next';
import { initReactI18next } from 'react-i18next';
import * as Localization from 'expo-localization';

import esMX from './es-MX.json';
import enUS from './en-US.json';

export const RESOURCES = {
  'es-MX': { translation: esMX },
  'en-US': { translation: enUS },
} as const;

export type SupportedLocale = keyof typeof RESOURCES;

let _instance: I18nInstance | null = null;

export function getI18n(): I18nInstance {
  if (_instance) return _instance;
  const deviceLocale =
    Localization.getLocales?.()[0]?.languageTag ??
    Localization.locale ??
    'es-MX';
  const initial: SupportedLocale = deviceLocale.startsWith('en') ? 'en-US' : 'es-MX';

  i18n
    .use(initReactI18next)
    .init({
      resources: RESOURCES,
      lng: initial,
      fallbackLng: 'es-MX',
      defaultNS: 'translation',
      interpolation: { escapeValue: false },
      returnNull: false,
      compatibilityJSON: 'v3',
    });
  _instance = i18n;
  return i18n;
}

/** Switch active language at runtime (used by Settings). */
export async function setLocale(locale: SupportedLocale): Promise<void> {
  const inst = getI18n();
  await inst.changeLanguage(locale);
}
