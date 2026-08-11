/**
 * Local persistence — MMKV (fast key-value, survives app restarts).
 *
 * Lazy-init so native TurboModule setup runs after the first React frame
 * (avoids blocking initial paint on bridgeless iOS).
 */
import { MMKV } from 'react-native-mmkv';

let _storage: MMKV | null = null;

export function getAppStorage(): MMKV {
  if (!_storage) {
    _storage = new MMKV({ id: 'casa-orquesta-voice' });
  }
  return _storage;
}

export const STORAGE_KEYS = {
  threads: 'threads.v1',
} as const;
