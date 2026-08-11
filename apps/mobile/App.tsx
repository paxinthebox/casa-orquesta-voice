/**
 * Casa·Orquesta · Voice — Mobile entry point.
 */
import 'react-native-gesture-handler';
import React, { useEffect } from 'react';
import { LogBox, StyleSheet, View } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { enableScreens } from 'react-native-screens';
import { StatusBar } from 'expo-status-bar';
import * as SplashScreen from 'expo-splash-screen';
import { Auth0Provider } from 'react-native-auth0';
import * as Notifications from 'expo-notifications';

import { RootNavigator } from './src/navigation/RootNavigator';
import { SessionProvider } from './src/state/SessionProvider';
import { VoiceProvider } from './src/voice/VoiceProvider';
import { ConsentGate } from './src/compliance/ConsentGate';
import { theme } from './src/theme';
import { getI18n } from './src/locale/i18n';
import { useThreadsStore } from './src/state/threadsStore';
import { useCardsStore } from './src/state/cardsStore';

enableScreens(true);
LogBox.ignoreLogs(['Open debugger to view warnings']);

function ThreadsBootstrap({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    useThreadsStore.getState().hydrate();
    useCardsStore.getState().syncFromActiveThread();
  }, []);
  return <View style={styles.flex}>{children}</View>;
}

getI18n();

const AUTH0_DOMAIN = process.env.EXPO_PUBLIC_AUTH0_DOMAIN ?? '';
const AUTH0_CLIENT_ID = process.env.EXPO_PUBLIC_AUTH0_CLIENT_ID ?? '';

function auth0Configured(): boolean {
  if (!AUTH0_DOMAIN.trim() || !AUTH0_CLIENT_ID.trim()) return false;
  if (/placeholder|example|your[-_]|xxx/i.test(AUTH0_DOMAIN)) return false;
  if (/placeholder|example|your[-_]|xxx/i.test(AUTH0_CLIENT_ID)) return false;
  return true;
}

const AUTH0_CONFIGURED = auth0Configured();

function AppProviders({ children }: { children: React.ReactNode }) {
  if (!AUTH0_CONFIGURED) {
    return <View style={styles.flex}>{children}</View>;
  }
  return (
    <Auth0Provider domain={AUTH0_DOMAIN} clientId={AUTH0_CLIENT_ID}>
      <View style={styles.flex}>{children}</View>
    </Auth0Provider>
  );
}

function MainApp() {
  return (
    <VoiceProvider>
      <RootNavigator />
    </VoiceProvider>
  );
}

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: false,
    shouldSetBadge: true,
  }),
});

export default function App() {
  useEffect(() => {
    void SplashScreen.hideAsync().catch(() => {});
    if (__DEV__) {
      console.log('[App] mounted', { auth0: AUTH0_CONFIGURED });
    }
  }, []);

  return (
    <View style={styles.root}>
      <SafeAreaProvider style={styles.root}>
        <GestureHandlerRootView style={styles.flex}>
          <View style={styles.shell}>
            <AppProviders>
              <SessionProvider>
                <ThreadsBootstrap>
                  <ConsentGate>
                    <MainApp />
                  </ConsentGate>
                </ThreadsBootstrap>
              </SessionProvider>
            </AppProviders>
            <StatusBar style="light" backgroundColor={theme.colors.navy} />
          </View>
        </GestureHandlerRootView>
      </SafeAreaProvider>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.colors.navy },
  shell: { flex: 1, backgroundColor: theme.colors.navy },
  flex: { flex: 1, backgroundColor: theme.colors.navy },
});
