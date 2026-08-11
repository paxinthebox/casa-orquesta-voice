/**
 * Permissions — Phase 3.2.
 *
 * Microphone permission flow with es-MX (or en-US) rationale before the
 * OS prompt. Apple specifically rewards apps that explain the *why*
 * before the system dialog appears — pass rates jump noticeably on
 * voice-first apps when this pattern is followed.
 *
 * Public API:
 *   await requestMicPermission()
 *     → 'granted' | 'denied' | 'undetermined'
 *
 * If the user has already denied, we resolve to 'denied' immediately
 * (without re-prompting — iOS rejects subsequent requests anyway) and
 * the caller is responsible for surfacing an "Open Settings" affordance.
 */
import { Platform, Linking } from 'react-native';

export type MicPermissionStatus = 'granted' | 'denied' | 'undetermined';

export interface RequestMicOptions {
  /**
   * Called before the OS dialog. Use to show your own pre-prompt UI
   * (e.g. a bottom sheet) with the rationale. Resolve `true` to
   * continue to the OS dialog, `false` to abort.
   *
   * If omitted, we skip straight to the OS prompt.
   */
  preprompt?: () => Promise<boolean>;
}

/** Single source of truth for mic permission. */
export async function requestMicPermission(
  opts: RequestMicOptions = {},
): Promise<MicPermissionStatus> {
  const initial = await getMicPermissionStatus();
  if (initial === 'granted') return 'granted';
  if (initial === 'denied') return 'denied';

  if (opts.preprompt) {
    const proceed = await opts.preprompt();
    if (!proceed) return 'undetermined';
  }
  return requestNative();
}

/** Read the current status without prompting. */
export async function getMicPermissionStatus(): Promise<MicPermissionStatus> {
  try {
    const { Audio } = await import('expo-av');
    const r = await Audio.getPermissionsAsync();
    return mapExpoStatus(r);
  } catch {
    // expo-av unavailable in dev — assume undetermined.
    return 'undetermined';
  }
}

async function requestNative(): Promise<MicPermissionStatus> {
  try {
    const { Audio } = await import('expo-av');
    const r = await Audio.requestPermissionsAsync();
    return mapExpoStatus(r);
  } catch {
    return 'undetermined';
  }
}

function mapExpoStatus(r: {
  granted?: boolean;
  status?: string;
  canAskAgain?: boolean;
}): MicPermissionStatus {
  if (r.granted) return 'granted';
  if (r.status === 'denied' && r.canAskAgain === false) return 'denied';
  if (r.status === 'denied') return 'denied';
  return 'undetermined';
}

/** Open the OS settings page for our app — used after a hard denial. */
export async function openAppSettings(): Promise<void> {
  if (Platform.OS === 'ios') {
    await Linking.openURL('app-settings:');
  } else {
    await Linking.openSettings();
  }
}
