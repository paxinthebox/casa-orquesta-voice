/**
 * RootNavigator — top-level navigation.
 *
 * Post-onboarding flow mirrors WhatsApp / Slack:
 *   Threads (inbox) → Chat (conversation) → Detail / ClientProfile / …
 */
import React from 'react';
import { StyleSheet, View } from 'react-native';
import { NavigationContainer, DefaultTheme, type Theme as NavTheme } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { useTranslation } from 'react-i18next';

import { OnboardingScreen } from '@/screens/OnboardingScreen';
import { ThreadsListScreen } from '@/screens/ThreadsListScreen';
import { ThreadChatScreen } from '@/screens/ThreadChatScreen';
import { SettingsScreen } from '@/screens/SettingsScreen';
import { DetailScreen } from '@/screens/DetailScreen';
import { VisitScheduleScreen } from '@/screens/VisitScheduleScreen';
import { ClientProfileScreen } from '@/screens/ClientProfileScreen';
import { useSession } from '@/state/SessionProvider';
import { colors, typography } from '@/theme';

export type RootStackParamList = {
  Onboarding: undefined;
  Threads: undefined;
  Chat: { threadId: string };
  Settings: undefined;
  Detail: { id?: string; kind?: 'listing' | 'audit' | 'slot' | 'people' } | undefined;
  VisitSchedule: { listingId: string; listingTitle?: string };
  ClientProfile: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();

const navTheme: NavTheme = {
  ...DefaultTheme,
  dark: true,
  colors: {
    ...DefaultTheme.colors,
    primary: colors.gold,
    background: colors.navy,
    card: colors.navyEl1,
    text: colors.textPrimary,
    border: colors.hairline,
    notification: colors.gold,
  },
};

export function RootNavigator() {
  const { t } = useTranslation();
  const onboardingComplete = useSession((s) => s.onboardingComplete);

  return (
    <View style={styles.root}>
      <NavigationContainer theme={navTheme}>
      <Stack.Navigator
        initialRouteName={onboardingComplete ? 'Threads' : 'Onboarding'}
        screenOptions={{
          headerStyle: { backgroundColor: colors.navy },
          headerTintColor: colors.textPrimary,
          headerTitleStyle: { ...typography.h3, color: colors.textPrimary },
          contentStyle: { backgroundColor: colors.navy },
        }}
      >
        <Stack.Screen
          name="Onboarding"
          component={OnboardingScreen}
          options={{ headerShown: false }}
        />
        <Stack.Screen
          name="Threads"
          component={ThreadsListScreen}
          options={{ headerShown: false }}
        />
        <Stack.Screen
          name="Chat"
          component={ThreadChatScreen}
          options={{ title: t('threads.title') }}
        />
        <Stack.Screen
          name="Settings"
          component={SettingsScreen}
          options={{ title: t('settings.title') }}
        />
        <Stack.Screen
          name="Detail"
          component={DetailScreen}
          options={{ title: '' }}
        />
        <Stack.Screen
          name="VisitSchedule"
          component={VisitScheduleScreen}
          options={{ title: t('visit.title') }}
        />
        <Stack.Screen
          name="ClientProfile"
          component={ClientProfileScreen}
          options={{ title: t('clientProfile.title') }}
        />
      </Stack.Navigator>
      </NavigationContainer>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
});
