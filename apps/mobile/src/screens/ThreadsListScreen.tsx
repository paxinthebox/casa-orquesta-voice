/**
 * ThreadsListScreen — WhatsApp / Slack-style inbox; tap a thread to open the chat.
 */
import React, { useCallback, useMemo, useState } from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  SafeAreaView,
  FlatList,
  ListRenderItem,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography, shadow } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { useThreadsStore, type ThreadMeta } from '@/state/threadsStore';
import { useCardsStore } from '@/state/cardsStore';
import { isClientProfileFilled, normalizeClientProfile, summarizeClientProfile } from '@/utils/clientProfile';
import {
  formatThreadListTime,
  getThreadPreview,
  threadAvatarInitial,
} from '@/utils/threadPreview';
import { ThreadManageModal } from '@/components/ThreadManageModal';

type Props = NativeStackScreenProps<RootStackParamList, 'Threads'>;

export function ThreadsListScreen({ navigation }: Props) {
  const { t, i18n } = useTranslation();
  const threads = useThreadsStore((s) => s.threads);
  const feeds = useThreadsStore((s) => s.feeds);
  const clientProfiles = useThreadsStore((s) => s.clientProfiles);
  const hydrate = useThreadsStore((s) => s.hydrate);
  const createThread = useThreadsStore((s) => s.createThread);
  const switchThread = useThreadsStore((s) => s.switchThread);
  const syncFromActiveThread = useCardsStore((s) => s.syncFromActiveThread);

  const [manageOpen, setManageOpen] = useState(false);

  useFocusEffect(
    useCallback(() => {
      hydrate();
    }, [hydrate]),
  );

  const sortedThreads = useMemo(
    () => [...threads].sort((a, b) => b.updatedAt - a.updatedAt),
    [threads],
  );

  const openChat = (threadId: string) => {
    switchThread(threadId);
    syncFromActiveThread();
    navigation.navigate('Chat', { threadId });
  };

  const startNewClient = () => {
    const id = createThread({ clientRole: 'buyer' });
    syncFromActiveThread();
    navigation.navigate('Chat', { threadId: id });
  };

  const roleLabel = (role: ThreadMeta['clientRole']) =>
    role === 'seller' ? t('threads.role_seller') : t('threads.role_buyer');

  const renderItem: ListRenderItem<ThreadMeta> = ({ item }) => {
    const rawProfile = clientProfiles[item.id];
    const profile = rawProfile ? normalizeClientProfile(rawProfile) : undefined;
    const profileChips = profile && isClientProfileFilled(profile)
      ? summarizeClientProfile(profile)
      : [];
    const preview = getThreadPreview(feeds[item.id], item.updatedAt);
    const previewText = preview.text
      || (profileChips.length ? profileChips.join(' · ') : t('threads.no_messages_preview'));
    const timeLabel = formatThreadListTime(
      preview.ts || item.updatedAt,
      i18n.language,
    );

    return (
      <Pressable
        style={styles.row}
        onPress={() => openChat(item.id)}
        onLongPress={() => {
          switchThread(item.id);
          syncFromActiveThread();
          setManageOpen(true);
        }}
        testID={`thread-row-${item.id}`}
      >
        <View style={styles.avatar}>
          <Text style={styles.avatarLabel}>{threadAvatarInitial(item.label)}</Text>
        </View>
        <View style={styles.rowBody}>
          <View style={styles.rowTop}>
            <Text style={styles.rowTitle} numberOfLines={1}>{item.label}</Text>
            {timeLabel ? <Text style={styles.rowTime}>{timeLabel}</Text> : null}
          </View>
          <Text style={styles.rowPreview} numberOfLines={1}>
            {roleLabel(item.clientRole)}
            {' · '}
            {previewText}
          </Text>
        </View>
      </Pressable>
    );
  };

  return (
    <SafeAreaView style={styles.root} testID="screen-threads">
      <View style={styles.toolbar}>
        <Text style={styles.toolbarTitle}>{t('threads.title')}</Text>
        <Pressable
          accessibilityRole="button"
          onPress={() => navigation.navigate('Settings')}
          style={styles.iconBtn}
          testID="threads-settings"
        >
          <Text style={styles.iconBtnLabel}>⚙</Text>
        </Pressable>
      </View>

      {sortedThreads.length === 0 ? (
        <View style={styles.empty} testID="threads-empty">
          <Text style={styles.emptyTitle}>{t('threads.empty_title')}</Text>
          <Text style={styles.emptySubtitle}>{t('threads.empty_subtitle')}</Text>
          <Pressable
            style={styles.primaryCta}
            onPress={startNewClient}
            accessibilityRole="button"
            testID="threads-new-client-search"
          >
            <Text style={styles.primaryCtaLabel}>{t('threads.new_client_search')}</Text>
          </Pressable>
        </View>
      ) : (
        <>
          <FlatList
            data={sortedThreads}
            keyExtractor={(item) => item.id}
            renderItem={renderItem}
            ItemSeparatorComponent={() => <View style={styles.separator} />}
            contentContainerStyle={styles.listContent}
            testID="threads-list"
          />
          <Pressable
            style={styles.fab}
            onPress={startNewClient}
            accessibilityRole="button"
            accessibilityLabel={t('threads.new_client_search')}
            testID="threads-fab-new"
          >
            <Text style={styles.fabLabel}>+</Text>
          </Pressable>
        </>
      )}

      <ThreadManageModal
        visible={manageOpen}
        onClose={() => setManageOpen(false)}
        navigation={navigation}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  toolbar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.m,
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline,
  },
  toolbarTitle: { ...typography.h2, color: colors.textPrimary },
  iconBtn: {
    width: 40,
    height: 40,
    borderRadius: radii.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.navyEl1,
    borderWidth: 1,
    borderColor: colors.hairline,
  },
  iconBtnLabel: { color: colors.textPrimary, fontSize: 18 },
  listContent: { paddingBottom: spacing.xxxl },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.m,
    backgroundColor: colors.navy,
  },
  avatar: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: colors.agentLocator,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.m,
  },
  avatarLabel: { ...typography.h3, color: colors.navy, fontWeight: '700' },
  rowBody: { flex: 1, minWidth: 0 },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.s },
  rowTitle: { ...typography.body, color: colors.textPrimary, fontWeight: '600', flex: 1 },
  rowTime: { ...typography.caption, color: colors.textMuted },
  rowPreview: { ...typography.caption, color: colors.textSecondary, marginTop: 2 },
  separator: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: colors.hairline,
    marginLeft: spacing.xl + 48 + spacing.m,
  },
  empty: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: spacing.xxl,
    paddingBottom: spacing.xxxl,
  },
  emptyTitle: { ...typography.h2, color: colors.textPrimary, textAlign: 'center' },
  emptySubtitle: {
    ...typography.body,
    color: colors.textSecondary,
    textAlign: 'center',
    marginTop: spacing.s,
    marginBottom: spacing.xxl,
  },
  primaryCta: {
    paddingVertical: spacing.l,
    paddingHorizontal: spacing.xl,
    borderRadius: radii.m,
    backgroundColor: colors.gold,
    alignItems: 'center',
  },
  primaryCtaLabel: { ...typography.body, color: colors.navy, fontWeight: '700' },
  fab: {
    position: 'absolute',
    right: spacing.xl,
    bottom: spacing.xl,
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.gold,
    alignItems: 'center',
    justifyContent: 'center',
    ...shadow.fab,
  },
  fabLabel: { fontSize: 28, lineHeight: 30, color: colors.navy, fontWeight: '300' },
});
