/**
 * MVP buyer.html action panels — message, call, schedule for people contacts.
 */
import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { useTranslation } from 'react-i18next';

import { colors, spacing, radii, typography } from '@/theme';
import type { PeopleCardData } from '@/state/cardsStore';
import {
  PERSON_AVAILABILITY_SLOTS,
  buildCallPrompt,
  buildSchedulePrompt,
  buildSendMessagePrompt,
  buildSyncCalendarsPrompt,
  defaultPersonMessage,
} from '@/utils/peopleFollowUp';

export interface PersonFollowUpPanelProps {
  person: PeopleCardData;
  onSendFollowUp: (prompt: string) => Promise<void>;
  busy?: boolean;
}

export function PersonFollowUpPanel({
  person,
  onSendFollowUp,
  busy = false,
}: PersonFollowUpPanelProps) {
  const { t } = useTranslation();
  const [message, setMessage] = useState(() => defaultPersonMessage(person));
  const [calendarsSynced, setCalendarsSynced] = useState(false);

  const kindKey = `person.followup.kind_${person.person_kind}` as const;

  const handleSendMessage = () => {
    void onSendFollowUp(buildSendMessagePrompt(person, message));
  };

  const handleCall = () => {
    void onSendFollowUp(buildCallPrompt(person));
  };

  const handleSyncCalendars = async () => {
    setCalendarsSynced(true);
    await onSendFollowUp(buildSyncCalendarsPrompt());
  };

  const handleSlot = (slot: string) => {
    void onSendFollowUp(buildSchedulePrompt(person, slot));
  };

  return (
    <View style={styles.wrap} testID="person-followup-panel">
      <Text style={styles.lead}>{t('person.followup.lead')}</Text>
      <Text style={styles.meta}>
        {t(kindKey)} · {person.id}
      </Text>

      <View style={styles.panel}>
        <Text style={styles.panelTitle}>{t('person.followup.message_title')}</Text>
        <Text style={styles.panelHint}>{t('person.followup.message_hint')}</Text>
        <TextInput
          style={styles.input}
          multiline
          value={message}
          onChangeText={setMessage}
          editable={!busy}
          testID="person-followup-message"
        />
        <Pressable
          style={[styles.btn, styles.btnPrimary, busy && styles.btnDisabled]}
          onPress={handleSendMessage}
          disabled={busy || !message.trim()}
          accessibilityRole="button"
          testID="person-followup-send"
        >
          {busy ? (
            <ActivityIndicator color={colors.navy} size="small" />
          ) : (
            <Text style={styles.btnPrimaryLabel}>{t('person.followup.send_message')}</Text>
          )}
        </Pressable>
      </View>

      <View style={styles.panel}>
        <Text style={styles.panelTitle}>{t('person.followup.call_title')}</Text>
        <Text style={styles.panelHint}>{t('person.followup.call_hint')}</Text>
        <View style={styles.callBox}>
          <Text style={styles.callLine}>
            <Text style={styles.callLabel}>{t('person.followup.contact')}: </Text>
            {person.name}
          </Text>
          <Text style={styles.callLine}>
            <Text style={styles.callLabel}>{t('person.followup.status')}: </Text>
            {t('person.followup.call_ready')}
          </Text>
        </View>
        <Pressable
          style={[styles.btn, styles.btnSecondary, busy && styles.btnDisabled]}
          onPress={handleCall}
          disabled={busy}
          accessibilityRole="button"
          testID="person-followup-call"
        >
          <Text style={styles.btnSecondaryLabel}>{t('person.followup.start_call')}</Text>
        </Pressable>
      </View>

      <View style={styles.panel}>
        <Text style={styles.panelTitle}>{t('person.followup.schedule_title')}</Text>
        <Text style={styles.panelHint}>{t('person.followup.schedule_hint')}</Text>
        <Text style={styles.syncLine}>
          {calendarsSynced
            ? t('person.followup.calendars_synced')
            : t('person.followup.calendars_default')}
        </Text>
        <Pressable
          style={[styles.btn, styles.btnGhost, busy && styles.btnDisabled]}
          onPress={() => { void handleSyncCalendars(); }}
          disabled={busy}
          accessibilityRole="button"
          testID="person-followup-sync"
        >
          <Text style={styles.btnGhostLabel}>{t('person.followup.sync_calendars')}</Text>
        </Pressable>
        <View style={styles.slotRow}>
          {PERSON_AVAILABILITY_SLOTS.map((slot) => (
            <Pressable
              key={slot}
              style={[styles.slotBtn, busy && styles.btnDisabled]}
              onPress={() => handleSlot(slot)}
              disabled={busy}
              accessibilityRole="button"
              testID={`person-followup-slot-${slot.replace(/\s+/g, '-')}`}
            >
              <Text style={styles.slotLabel}>{slot}</Text>
            </Pressable>
          ))}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.m, marginTop: spacing.m },
  lead: { ...typography.body, color: colors.textSecondary },
  meta: { ...typography.caption, color: colors.textMuted },
  panel: {
    padding: spacing.m,
    borderRadius: radii.m,
    backgroundColor: colors.navy,
    borderWidth: 1,
    borderColor: colors.hairline,
    gap: spacing.s,
  },
  panelTitle: { ...typography.bodyBold, color: colors.textPrimary },
  panelHint: { ...typography.caption, color: colors.textMuted },
  input: {
    minHeight: 88,
    padding: spacing.m,
    borderRadius: radii.s,
    borderWidth: 1,
    borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
    color: colors.textPrimary,
    ...typography.body,
    textAlignVertical: 'top',
  },
  callBox: {
    padding: spacing.m,
    borderRadius: radii.s,
    backgroundColor: colors.navyEl1,
    gap: spacing.xs,
  },
  callLine: { ...typography.body, color: colors.textPrimary },
  callLabel: { ...typography.bodyBold, color: colors.textSecondary },
  btn: {
    paddingVertical: spacing.m,
    paddingHorizontal: spacing.l,
    borderRadius: radii.pill,
    alignItems: 'center',
  },
  btnPrimary: { backgroundColor: colors.agentLocator },
  btnPrimaryLabel: { ...typography.bodyBold, color: colors.navy },
  btnSecondary: {
    borderWidth: 1,
    borderColor: '#5B8DEF',
  },
  btnSecondaryLabel: { ...typography.bodyBold, color: '#5B8DEF' },
  btnGhost: {
    borderWidth: 1,
    borderColor: colors.hairline,
  },
  btnGhostLabel: { ...typography.body, color: colors.textPrimary },
  btnDisabled: { opacity: 0.5 },
  syncLine: { ...typography.caption, color: colors.textSecondary },
  slotRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.s },
  slotBtn: {
    paddingVertical: spacing.s,
    paddingHorizontal: spacing.m,
    borderRadius: radii.s,
    borderWidth: 1,
    borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
  },
  slotLabel: { ...typography.caption, color: colors.textPrimary },
});
