/**
 * ThreadManageModal — rename, role, profile, delete for the active client thread.
 */
import React, { useState } from 'react';
import {
  Modal,
  View,
  Text,
  Pressable,
  StyleSheet,
  TextInput,
  ScrollView,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { useThreadsStore, type ClientRole, type ThreadMeta } from '@/state/threadsStore';
import { useCardsStore } from '@/state/cardsStore';
import { isClientProfileFilled, summarizeClientProfile } from '@/utils/clientProfile';

type ThreadNav = NativeStackNavigationProp<RootStackParamList>;

function RoleToggle({
  value,
  onChange,
  testIdPrefix,
}: {
  value: ClientRole;
  onChange: (role: ClientRole) => void;
  testIdPrefix: string;
}) {
  const { t } = useTranslation();
  const options: ClientRole[] = ['buyer', 'seller'];

  return (
    <View style={styles.roleRow}>
      {options.map((role) => {
        const selected = value === role;
        return (
          <Pressable
            key={role}
            style={[styles.rolePill, selected && styles.rolePillActive]}
            onPress={() => onChange(role)}
            testID={`${testIdPrefix}-role-${role}`}
          >
            <Text style={[styles.rolePillLabel, selected && styles.rolePillLabelActive]}>
              {role === 'seller' ? t('threads.role_seller') : t('threads.role_buyer')}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

export interface ThreadManageModalProps {
  visible: boolean;
  onClose: () => void;
  navigation: Pick<ThreadNav, 'navigate'>;
}

export function ThreadManageModal({ visible, onClose, navigation }: ThreadManageModalProps) {
  const { t } = useTranslation();
  const threads = useThreadsStore((s) => s.threads);
  const activeThreadId = useThreadsStore((s) => s.activeThreadId);
  const clientProfiles = useThreadsStore((s) => s.clientProfiles);
  const getClientProfile = useThreadsStore((s) => s.getClientProfile);
  const createThread = useThreadsStore((s) => s.createThread);
  const switchThread = useThreadsStore((s) => s.switchThread);
  const deleteThread = useThreadsStore((s) => s.deleteThread);
  const renameActiveThread = useThreadsStore((s) => s.renameActiveThread);
  const setActiveThreadRole = useThreadsStore((s) => s.setActiveThreadRole);
  const syncFromActiveThread = useCardsStore((s) => s.syncFromActiveThread);

  const [newLabel, setNewLabel] = useState('');
  const [newRole, setNewRole] = useState<ClientRole>('buyer');

  const active = threads.find((th) => th.id === activeThreadId) ?? null;
  const canDelete = threads.length > 0 && !!active;

  const handleSwitch = (threadId: string) => {
    if (threadId === activeThreadId) return;
    switchThread(threadId);
    syncFromActiveThread();
  };

  const handleCreate = () => {
    const label = newLabel.trim() || undefined;
    createThread({ label, clientRole: newRole });
    syncFromActiveThread();
    setNewLabel('');
    setNewRole('buyer');
  };

  const handleDeleteActive = () => {
    if (!active || !canDelete) return;
    if (deleteThread(active.id)) {
      syncFromActiveThread();
      onClose();
    }
  };

  const roleLabel = (role: ClientRole) =>
    role === 'seller' ? t('threads.role_seller') : t('threads.role_buyer');

  const activeProfile = active ? getClientProfile(active.id) : null;
  const activeProfileFilled = activeProfile ? isClientProfileFilled(activeProfile) : false;
  const activeProfileChips = activeProfile ? summarizeClientProfile(activeProfile) : [];

  const openProfile = () => {
    onClose();
    navigation.navigate('ClientProfile');
  };

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={styles.sheet} onPress={(e) => e.stopPropagation()}>
          <Text style={styles.title}>{t('threads.title')}</Text>
          <Text style={styles.subtitle}>{t('threads.subtitle')}</Text>

          <ScrollView style={styles.list}>
            {threads.map((th: ThreadMeta) => {
              const selected = th.id === activeThreadId;
              const profile = clientProfiles[th.id];
              const profileChips = profile && isClientProfileFilled(profile)
                ? summarizeClientProfile(profile)
                : [];
              return (
                <Pressable
                  key={th.id}
                  style={[styles.row, selected && styles.rowActive]}
                  onPress={() => handleSwitch(th.id)}
                  testID={`thread-row-${th.id}`}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowLabel}>{th.label}</Text>
                    <Text style={styles.rowMeta}>{roleLabel(th.clientRole)}</Text>
                    {profileChips.length ? (
                      <Text style={styles.rowProfile} numberOfLines={1}>
                        {profileChips.join(' · ')}
                      </Text>
                    ) : (
                      <Text style={styles.rowProfileEmpty}>
                        {t('threads.profile_empty')}
                      </Text>
                    )}
                  </View>
                  {selected ? <Text style={styles.check}>✓</Text> : null}
                </Pressable>
              );
            })}
          </ScrollView>

          {active ? (
            <View style={styles.renameBlock}>
              <Text style={styles.renameHint}>{t('threads.rename_hint')}</Text>
              <TextInput
                style={styles.input}
                defaultValue={active.label}
                placeholder={t('threads.name_placeholder')}
                placeholderTextColor={colors.textMuted}
                onSubmitEditing={(e) => renameActiveThread(e.nativeEvent.text)}
                returnKeyType="done"
              />
              <Text style={styles.roleHint}>{t('threads.role_hint')}</Text>
              <RoleToggle
                value={active.clientRole}
                onChange={setActiveThreadRole}
                testIdPrefix="thread-active"
              />
              <Pressable
                style={styles.profileBtn}
                onPress={openProfile}
                testID="thread-open-profile"
              >
                <Text style={styles.profileBtnLabel}>
                  {activeProfileFilled
                    ? t('threads.profile_edit')
                    : t('threads.profile_create')}
                </Text>
              </Pressable>
              {activeProfileFilled && activeProfileChips.length ? (
                <Text style={styles.profilePreview} numberOfLines={2}>
                  {activeProfileChips.join(' · ')}
                </Text>
              ) : null}
              {canDelete ? (
                <Pressable
                  style={styles.deleteBtn}
                  onPress={handleDeleteActive}
                  testID="thread-delete-active"
                >
                  <Text style={styles.deleteBtnLabel}>{t('threads.delete_button')}</Text>
                </Pressable>
              ) : null}
            </View>
          ) : null}

          <View style={styles.newBlock}>
            <Text style={styles.newHeading}>{t('threads.new_heading')}</Text>
            <RoleToggle
              value={newRole}
              onChange={setNewRole}
              testIdPrefix="thread-new"
            />
            <View style={styles.newRow}>
              <TextInput
                style={[styles.input, { flex: 1 }]}
                value={newLabel}
                onChangeText={setNewLabel}
                placeholder={t('threads.new_placeholder')}
                placeholderTextColor={colors.textMuted}
              />
              <Pressable style={styles.addBtn} onPress={handleCreate} testID="thread-create">
                <Text style={styles.addBtnLabel}>{t('threads.new_button')}</Text>
              </Pressable>
            </View>
          </View>

          <Pressable style={styles.closeBtn} onPress={onClose}>
            <Text style={styles.closeBtnLabel}>{t('common.close')}</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.55)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: colors.navyEl1,
    borderTopLeftRadius: radii.xl,
    borderTopRightRadius: radii.xl,
    padding: spacing.xl,
    maxHeight: '85%',
    borderWidth: 1,
    borderColor: colors.hairline,
  },
  title: { ...typography.h2, color: colors.textPrimary },
  subtitle: { ...typography.body, color: colors.textSecondary, marginTop: spacing.xs },
  list: { marginTop: spacing.l, maxHeight: 200 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: spacing.m,
    borderRadius: radii.m,
    marginBottom: spacing.xs,
    backgroundColor: colors.navyEl2,
  },
  rowActive: { borderWidth: 1, borderColor: colors.agentLocator },
  rowLabel: { ...typography.body, color: colors.textPrimary },
  rowMeta: { ...typography.caption, color: colors.textMuted, marginTop: 2 },
  rowProfile: { ...typography.caption, color: colors.agentLocator, marginTop: 4 },
  rowProfileEmpty: { ...typography.caption, color: colors.textMuted, marginTop: 4, fontStyle: 'italic' },
  check: { color: colors.agentLocator, fontSize: 18, fontWeight: '700' },
  renameBlock: { marginTop: spacing.m },
  renameHint: { ...typography.caption, color: colors.textMuted, marginBottom: spacing.xs },
  roleHint: {
    ...typography.caption,
    color: colors.textMuted,
    marginTop: spacing.m,
    marginBottom: spacing.xs,
  },
  roleRow: { flexDirection: 'row', gap: spacing.s },
  rolePill: {
    flex: 1,
    paddingVertical: spacing.s,
    paddingHorizontal: spacing.m,
    borderRadius: radii.m,
    backgroundColor: colors.navyEl2,
    borderWidth: 1,
    borderColor: colors.hairline,
    alignItems: 'center',
  },
  rolePillActive: {
    borderColor: colors.gold,
    backgroundColor: colors.goldFaint,
  },
  rolePillLabel: { ...typography.caption, color: colors.textSecondary },
  rolePillLabelActive: { color: colors.gold, fontWeight: '600' },
  profileBtn: {
    marginTop: spacing.m,
    paddingVertical: spacing.m,
    borderRadius: radii.m,
    borderWidth: 1,
    borderColor: colors.agentLocator,
    alignItems: 'center',
  },
  profileBtnLabel: { ...typography.caption, color: colors.agentLocator, fontWeight: '600' },
  profilePreview: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.s },
  deleteBtn: {
    marginTop: spacing.m,
    paddingVertical: spacing.m,
    borderRadius: radii.m,
    borderWidth: 1,
    borderColor: colors.danger,
    alignItems: 'center',
  },
  deleteBtnLabel: { ...typography.caption, color: colors.danger, fontWeight: '600' },
  newBlock: { marginTop: spacing.l },
  newHeading: { ...typography.caption, color: colors.textSecondary, marginBottom: spacing.xs },
  newRow: { flexDirection: 'row', gap: spacing.s, marginTop: spacing.s, alignItems: 'center' },
  input: {
    ...typography.body,
    color: colors.textPrimary,
    backgroundColor: colors.navyEl2,
    borderRadius: radii.m,
    paddingHorizontal: spacing.m,
    paddingVertical: spacing.s,
    borderWidth: 1,
    borderColor: colors.hairline,
  },
  addBtn: {
    backgroundColor: colors.agentLocator,
    paddingHorizontal: spacing.m,
    paddingVertical: spacing.m,
    borderRadius: radii.m,
  },
  addBtnLabel: { ...typography.caption, color: colors.navy, fontWeight: '600' },
  closeBtn: { marginTop: spacing.l, alignItems: 'center', padding: spacing.m },
  closeBtnLabel: { ...typography.body, color: colors.textSecondary },
});
