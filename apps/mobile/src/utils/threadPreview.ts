/** Preview line + timestamp for thread list rows (WhatsApp / Slack style). */
import type { ThreadFeedSnapshot } from '@/state/threadsStore';
import { sortTimeline, type CardData, type TimelineItem } from '@/state/cardsStore';

export interface ThreadPreview {
  text: string;
  ts: number;
}

function cardPreview(card: CardData): string {
  switch (card.kind) {
    case 'listing':
      return `🏠 ${card.title}`;
    case 'people':
      return `👤 ${card.name}`;
    case 'audit':
      return `📋 ${card.headline}`;
    case 'slot':
      return `📅 ${card.agent_name}`;
    default:
      return '';
  }
}

function previewFromTimeline(timeline: TimelineItem[]): ThreadPreview {
  const sorted = sortTimeline(timeline);
  for (let i = sorted.length - 1; i >= 0; i--) {
    const item = sorted[i];
    if (!item) continue;
    if (item.kind === 'message' && item.text.trim()) {
      return { text: item.text.trim(), ts: item.ts };
    }
    if (item.kind === 'card') {
      const text = cardPreview(item.card);
      if (text) return { text, ts: item.ts };
    }
  }
  return { text: '', ts: 0 };
}

export function getThreadPreview(
  feed: ThreadFeedSnapshot | undefined,
  threadUpdatedAt: number,
): ThreadPreview {
  const fromFeed = previewFromTimeline(feed?.timeline ?? []);
  if (fromFeed.text) return fromFeed;
  return { text: '', ts: threadUpdatedAt };
}

export function formatThreadListTime(ts: number, locale = 'es-MX'): string {
  if (!ts) return '';
  const date = new Date(ts);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const dayMs = 24 * 60 * 60 * 1000;

  try {
    if (startOfDate.getTime() === startOfToday.getTime()) {
      return new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit' }).format(date);
    }
    if (startOfToday.getTime() - startOfDate.getTime() === dayMs) {
      return locale.startsWith('es') ? 'Ayer' : 'Yesterday';
    }
    if (now.getTime() - date.getTime() < 7 * dayMs) {
      return new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(date);
    }
    return new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(date);
  } catch {
    return '';
  }
}

export function threadAvatarInitial(label: string): string {
  const trimmed = label.trim();
  if (!trimmed) return '?';
  return trimmed.charAt(0).toUpperCase();
}
