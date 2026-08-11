/**
 * ThreadChatScreen — single client conversation (messages, cards, voice).
 */
import React, { useRef, useCallback, useMemo, useLayoutEffect } from 'react';
import {
  View,
  Text,
  Pressable,
  StyleSheet,
  SafeAreaView,
  ScrollView,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { MicButton } from '@/components/MicButton';
import { ClientProfileSummary } from '@/components/ClientProfileSummary';
import { AgentChips } from '@/components/AgentChips';
import { ListingCard } from '@/components/ListingCard';
import { SwipeableListingCard } from '@/components/SwipeableListingCard';
import { SavedListingsSection } from '@/components/SavedListingsSection';
import { SlotCard } from '@/components/SlotCard';
import { AuditCard } from '@/components/AuditCard';
import { PeopleCard } from '@/components/PeopleCard';
import { MessageBubble } from '@/components/MessageBubble';
import { ThreadManageModal } from '@/components/ThreadManageModal';
import { useVoice } from '@/voice/VoiceProvider';
import { useCardsStore, sortTimeline, shouldShowStreamingReply, type CardData, type ListingCardData } from '@/state/cardsStore';
import { useThreadsStore, selectActiveThread } from '@/state/threadsStore';
import {
  countPendingListingCards,
  discardedListingIdSet,
  filterPendingListingTimeline,
  savedListingIds,
} from '@/utils/listingMatch';

type Props = NativeStackScreenProps<RootStackParamList, 'Chat'>;

export function ThreadChatScreen({ navigation, route }: Props) {
  const { t } = useTranslation();
  const { transcriptPartial, replyPartial, bootstrapSession } = useVoice();
  const hydrateThreads = useThreadsStore((s) => s.hydrate);
  const activeThread = useThreadsStore(selectActiveThread);
  const switchThread = useThreadsStore((s) => s.switchThread);
  const syncFromActiveThread = useCardsStore((s) => s.syncFromActiveThread);
  const saveListing = useCardsStore((s) => s.saveListing);
  const discardListing = useCardsStore((s) => s.discardListing);
  const unsaveListing = useCardsStore((s) => s.unsaveListing);
  const bootstrapRef = useRef(bootstrapSession);
  bootstrapRef.current = bootstrapSession;
  const timeline = useCardsStore((s) => s.timeline);
  const savedListings = useCardsStore((s) => s.savedListings ?? []);
  const discardedListingIds = useCardsStore((s) => s.discardedListingIds ?? []);
  const savedIds = useMemo(() => savedListingIds(savedListings), [savedListings]);
  const discardedIds = useMemo(
    () => discardedListingIdSet(discardedListingIds),
    [discardedListingIds],
  );
  const visibleTimeline = useMemo(
    () => filterPendingListingTimeline(timeline, savedIds, discardedIds),
    [timeline, savedIds, discardedIds],
  );
  const orderedTimeline = useMemo(() => sortTimeline(visibleTimeline), [visibleTimeline]);
  const pendingListingCount = useMemo(
    () => countPendingListingCards(timeline, savedIds, discardedIds),
    [timeline, savedIds, discardedIds],
  );
  const scrollRef = useRef<ScrollView>(null);
  const [manageOpen, setManageOpen] = React.useState(false);

  const threadId = route.params?.threadId;

  React.useEffect(() => {
    if (threadId) {
      switchThread(threadId);
      syncFromActiveThread();
    }
  }, [threadId, switchThread, syncFromActiveThread]);

  useFocusEffect(
    useCallback(() => {
      hydrateThreads();
      syncFromActiveThread();
      void bootstrapRef.current();
    }, [hydrateThreads, syncFromActiveThread]),
  );

  useLayoutEffect(() => {
    navigation.setOptions({
      title: activeThread?.label ?? t('threads.title'),
      headerRight: () => (
        <Pressable
          onPress={() => setManageOpen(true)}
          style={styles.headerBtn}
          accessibilityRole="button"
          testID="chat-manage"
        >
          <Text style={styles.headerBtnLabel}>⋯</Text>
        </Pressable>
      ),
    });
  }, [navigation, activeThread?.label, t]);

  const hasTimeline = orderedTimeline.length > 0
    || savedListings.length > 0
    || transcriptPartial
    || shouldShowStreamingReply(orderedTimeline, replyPartial);

  React.useEffect(() => {
    scrollRef.current?.scrollToEnd({ animated: true });
  }, [orderedTimeline.length, transcriptPartial, replyPartial]);

  const renderCard = (card: CardData) => {
    switch (card.kind) {
      case 'listing':
        return (
          <SwipeableListingCard
            key={card.id}
            testID={`swipe-listing-${card.id}`}
            onSave={() => saveListing(card)}
            onDiscard={() => discardListing(card.id)}
          >
            <ListingCard
              listing={card}
              onPress={(listing) => navigation.navigate('Detail', { id: listing.id, kind: 'listing' })}
            />
          </SwipeableListingCard>
        );
      case 'slot':
        return (
          <SlotCard
            key={card.id}
            slot={card}
            onPress={() =>
              navigation.navigate('Detail', {
                id: card.listing_id ?? card.id,
                kind: card.listing_id ? 'listing' : 'slot',
              })
            }
          />
        );
      case 'audit':
        return (
          <AuditCard
            key={card.id}
            audit={card}
            onPress={() => navigation.navigate('Detail', { id: card.document_id, kind: 'audit' })}
          />
        );
      case 'people':
        return (
          <PeopleCard
            key={card.id}
            person={card}
            onSelect={() => navigation.navigate('Detail', { id: card.id, kind: 'people' })}
          />
        );
      default:
        return null;
    }
  };

  if (!activeThread) {
    return (
      <SafeAreaView style={styles.root}>
        <View style={styles.fallback}>
          <Text style={styles.fallbackText}>{t('threads.empty_title')}</Text>
          <Pressable
            style={styles.fallbackBtn}
            onPress={() => navigation.navigate('Threads')}
          >
            <Text style={styles.fallbackBtnLabel}>{t('threads.back_to_list')}</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.root} testID="screen-chat">
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
        onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
      >
        <Text style={styles.prompt}>{t('home.prompt')}</Text>

        <AgentChips />

        <ClientProfileSummary navigation={navigation} />

        {hasTimeline ? (
          <View style={styles.feed} testID="home-feed">
            {pendingListingCount > 0 ? (
              <Text style={styles.swipeHint}>{t('cards.swipe_hint')}</Text>
            ) : null}
            {orderedTimeline.map((entry) => {
              if (entry.kind === 'message') {
                return (
                  <MessageBubble
                    key={entry.id}
                    role={entry.role}
                    text={entry.text}
                    ts={entry.ts}
                  />
                );
              }
              return (
                <View key={entry.id}>{renderCard(entry.card)}</View>
              );
            })}
            {transcriptPartial ? (
              <MessageBubble role="user" text={transcriptPartial} ts={Date.now()} />
            ) : null}
            {shouldShowStreamingReply(orderedTimeline, replyPartial) ? (
              <MessageBubble role="assistant" text={replyPartial} ts={Date.now()} />
            ) : null}
            <SavedListingsSection
              listings={savedListings}
              onUnsave={unsaveListing}
              onOpenListing={(listing: ListingCardData) =>
                navigation.navigate('Detail', { id: listing.id, kind: 'listing' })}
            />
          </View>
        ) : (
          <View style={styles.empty}>
            <Text style={styles.emptyLabel}>{t('home.empty_feed')}</Text>
          </View>
        )}
      </ScrollView>

      <View style={styles.micBar} pointerEvents="box-none">
        <MicButton testID="home-mic" />
      </View>

      <ThreadManageModal
        visible={manageOpen}
        onClose={() => setManageOpen(false)}
        navigation={navigation}
      />
    </SafeAreaView>
  );
}

/** @deprecated Use ThreadChatScreen — kept for sanity_check imports. */
export const HomeScreen = ThreadChatScreen;

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  scroll: {
    padding: spacing.xl,
    gap: spacing.l,
    paddingBottom: 220,
  },
  prompt: { ...typography.body, color: colors.textSecondary },
  feed: { marginTop: spacing.s, gap: spacing.xs },
  swipeHint: {
    ...typography.caption,
    color: colors.textMuted,
    textAlign: 'center',
    marginBottom: spacing.s,
  },
  empty: {
    marginTop: spacing.xl,
    padding: spacing.xl,
    borderRadius: radii.l,
    backgroundColor: colors.navyEl1,
    borderWidth: 1,
    borderColor: colors.hairline,
    alignItems: 'center',
  },
  emptyLabel: { ...typography.body, color: colors.textSecondary, textAlign: 'center' },
  micBar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    paddingBottom: spacing.xl,
    alignItems: 'center',
    backgroundColor: 'transparent',
  },
  headerBtn: { paddingHorizontal: spacing.m, paddingVertical: spacing.xs },
  headerBtnLabel: { color: colors.textPrimary, fontSize: 22, lineHeight: 24 },
  fallback: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.xl },
  fallbackText: { ...typography.body, color: colors.textSecondary, textAlign: 'center' },
  fallbackBtn: {
    marginTop: spacing.l,
    paddingVertical: spacing.m,
    paddingHorizontal: spacing.xl,
    borderRadius: radii.m,
    backgroundColor: colors.gold,
  },
  fallbackBtnLabel: { ...typography.body, color: colors.navy, fontWeight: '600' },
});
