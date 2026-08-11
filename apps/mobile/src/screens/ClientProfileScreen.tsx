/**
 * ClientProfileScreen — MVP buyer.html "Perfil del cliente" formulary.
 *
 * Captures search criteria, budget analysis, and credit/document follow-up,
 * then submits via sendFollowUpMessage (same path as people follow-up).
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Alert,
} from 'react-native';
import { useTranslation } from 'react-i18next';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { colors, spacing, radii, typography } from '@/theme';
import type { RootStackParamList } from '@/navigation/RootNavigator';
import { FormSelect } from '@/components/FormSelect';
import { useThreadsStore, selectActiveThread } from '@/state/threadsStore';
import { useVoice } from '@/voice/VoiceProvider';
import {
  applyLoanDefaults,
  budgetAnalysis,
  buildClientSearchPrompt,
  buildClientSearchDisplayLabel,
  clientProfileToWire,
  CLIENT_PROFILE_FEATURES,
  CLIENT_PROFILE_PROPERTY_TYPES,
  emptyClientProfile,
  formatMoneyMxn,
  normalizeClientProfile,
  togglePropertyType,
  type BudgetAnalysis,
  type ClientProfileDraft,
  type LoanType,
  type ListingMode,
  type PreapprovalStatus,
  type PropertyType,
} from '@/utils/clientProfile';

type Props = NativeStackScreenProps<RootStackParamList, 'ClientProfile'>;

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function BudgetRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.budgetRow}>
      <Text style={styles.budgetLabel}>{label}</Text>
      <Text style={styles.budgetValue}>{value}</Text>
    </View>
  );
}

function ClientProfileBuyPanels({
  profile,
  analysis,
  onUpdate,
  preapprovalOptions,
}: {
  profile: ClientProfileDraft;
  analysis: BudgetAnalysis;
  onUpdate: (patch: Partial<ClientProfileDraft>) => void;
  preapprovalOptions: { value: PreapprovalStatus; label: string }[];
}) {
  const { t } = useTranslation();

  return (
    <>
      <View style={styles.panel}>
        <Text style={styles.panelTitle}>{t('clientProfile.budget_title')}</Text>
        <Text style={styles.panelHint}>{t('clientProfile.budget_hint')}</Text>
        <View style={styles.row}>
          <View style={styles.third}>
            <Field label={t('clientProfile.notary_rate')}>
              <TextInput
                style={styles.input}
                value={profile.notaryRate}
                onChangeText={(notaryRate) => onUpdate({ notaryRate })}
                keyboardType="decimal-pad"
                testID="client-profile-notary"
              />
            </Field>
          </View>
          <View style={styles.third}>
            <Field label={t('clientProfile.down_rate')}>
              <TextInput
                style={styles.input}
                value={profile.downRate}
                onChangeText={(downRate) => onUpdate({ downRate })}
                keyboardType="decimal-pad"
                testID="client-profile-down"
              />
            </Field>
          </View>
          <View style={styles.third}>
            <Field label={t('clientProfile.closing_rate')}>
              <TextInput
                style={styles.input}
                value={profile.closingRate}
                onChangeText={(closingRate) => onUpdate({ closingRate })}
                keyboardType="decimal-pad"
                testID="client-profile-closing"
              />
            </Field>
          </View>
        </View>
        {analysis.propertyValue ? (
          <View style={styles.budgetGrid}>
            <BudgetRow
              label={t('clientProfile.budget_property')}
              value={formatMoneyMxn(analysis.propertyValue)}
            />
            <BudgetRow
              label={t('clientProfile.budget_notary', { rate: analysis.notaryRate })}
              value={formatMoneyMxn(analysis.notaryFees)}
            />
            <BudgetRow
              label={t('clientProfile.budget_down', { rate: analysis.downRate })}
              value={formatMoneyMxn(analysis.downPayment)}
            />
            <BudgetRow
              label={t('clientProfile.budget_closing', { rate: analysis.closingRate })}
              value={formatMoneyMxn(analysis.closingCosts)}
            />
            <BudgetRow
              label={t('clientProfile.budget_upfront')}
              value={formatMoneyMxn(analysis.upfrontCash)}
            />
            <BudgetRow
              label={t('clientProfile.budget_total')}
              value={formatMoneyMxn(analysis.totalAcquisitionCost)}
            />
            <BudgetRow
              label={t('clientProfile.budget_financed')}
              value={formatMoneyMxn(analysis.financedAmount)}
            />
          </View>
        ) : (
          <Text style={styles.pending}>{t('clientProfile.budget_pending')}</Text>
        )}
      </View>

      <View style={styles.panel}>
        <Text style={styles.panelTitle}>{t('clientProfile.credit_title')}</Text>
        <Text style={styles.panelHint}>{t('clientProfile.credit_hint')}</Text>

        <Field label={t('clientProfile.credit_broker')}>
          <TextInput
            style={styles.input}
            value={profile.creditBroker}
            onChangeText={(creditBroker) => onUpdate({ creditBroker })}
            placeholder={t('clientProfile.credit_broker_ph')}
            placeholderTextColor={colors.textMuted}
            testID="client-profile-credit-broker"
          />
        </Field>

        <Field label={t('clientProfile.credit_broker_contact')}>
          <TextInput
            style={styles.input}
            value={profile.creditBrokerContact}
            onChangeText={(creditBrokerContact) => onUpdate({ creditBrokerContact })}
            placeholder={t('clientProfile.credit_broker_contact_ph')}
            placeholderTextColor={colors.textMuted}
            testID="client-profile-credit-contact"
          />
        </Field>

        <FormSelect
          label={t('clientProfile.preapproval_status')}
          value={profile.preapprovalStatus}
          options={preapprovalOptions}
          onChange={(preapprovalStatus) => onUpdate({ preapprovalStatus })}
          testID="client-profile-preapproval"
        />

        <Field label={t('clientProfile.preapproval_date')}>
          <TextInput
            style={styles.input}
            value={profile.preapprovalDate}
            onChangeText={(preapprovalDate) => onUpdate({ preapprovalDate })}
            placeholder="YYYY-MM-DD"
            placeholderTextColor={colors.textMuted}
            testID="client-profile-preapproval-date"
          />
        </Field>

        <Field label={t('clientProfile.preapproval_notes')}>
          <TextInput
            style={styles.textArea}
            multiline
            value={profile.preapprovalNotes}
            onChangeText={(preapprovalNotes) => onUpdate({ preapprovalNotes })}
            placeholder={t('clientProfile.preapproval_notes_ph')}
            placeholderTextColor={colors.textMuted}
            textAlignVertical="top"
            testID="client-profile-preapproval-notes"
          />
        </Field>

        <Pressable
          style={styles.checkRow}
          onPress={() => onUpdate({ consumerAgreement: !profile.consumerAgreement })}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: profile.consumerAgreement }}
          testID="client-profile-consumer-agreement"
        >
          <View style={[styles.checkbox, profile.consumerAgreement && styles.checkboxOn]}>
            {profile.consumerAgreement ? (
              <Text style={styles.checkMark}>✓</Text>
            ) : null}
          </View>
          <Text style={styles.checkLabel}>{t('clientProfile.consumer_agreement')}</Text>
        </Pressable>
      </View>
    </>
  );
}

export function ClientProfileScreen({ navigation }: Props) {
  const { t } = useTranslation();
  const { sendFollowUpMessage, status, syncThreadContext } = useVoice();
  const activeThread = useThreadsStore(selectActiveThread);
  const setActiveClientProfile = useThreadsStore((s) => s.setActiveClientProfile);
  const ensureActiveThread = useThreadsStore((s) => s.ensureActiveThread);
  const switchThread = useThreadsStore((s) => s.switchThread);
  const renameActiveThread = useThreadsStore((s) => s.renameActiveThread);

  const [profile, setProfile] = useState<ClientProfileDraft>(emptyClientProfile);
  const profileRef = useRef(profile);
  profileRef.current = profile;
  const busy = status === 'thinking' || status === 'speaking';

  useFocusEffect(
    useCallback(() => {
      if (!activeThread?.id) return;
      const stored = useThreadsStore.getState().getActiveClientProfile();
      setProfile(normalizeClientProfile(stored));
      return () => {
        useThreadsStore.getState().setActiveClientProfile(profileRef.current);
      };
    }, [activeThread?.id]),
  );

  const updateProfile = useCallback((patch: Partial<ClientProfileDraft>) => {
    setProfile((prev) => ({ ...prev, ...patch }));
  }, []);

  const onLoanChange = (loanType: LoanType) => {
    setProfile((prev) => applyLoanDefaults({ ...prev, loanType }));
  };

  const analysis = useMemo(() => budgetAnalysis(profile), [profile]);

  const toggleFeature = (feature: string) => {
    setProfile((prev) => {
      const features = prev.features.includes(feature)
        ? prev.features.filter((f) => f !== feature)
        : [...prev.features, feature];
      return { ...prev, features };
    });
  };

  const onClear = () => {
    const blank = emptyClientProfile();
    setProfile(blank);
    setActiveClientProfile(blank);
    syncThreadContext();
  };

  const onSubmit = async () => {
    const saved = normalizeClientProfile(profile);
    if (saved.listingMode !== 'rent' && saved.listingMode !== 'sale') {
      Alert.alert(
        t('clientProfile.listing_mode'),
        t('clientProfile.listing_mode_hint'),
      );
      return;
    }
    setProfile(saved);
    const threadId = ensureActiveThread({ clientRole: 'buyer' });
    switchThread(threadId);
    setActiveClientProfile(saved);
    syncThreadContext();
    const prompt = buildClientSearchPrompt(saved);
    const wireProfile = clientProfileToWire(saved);
    const name = saved.clientName.trim();
    if (name) renameActiveThread(name);
    navigation.navigate('Chat', { threadId });
    await sendFollowUpMessage(prompt, {
      displayText: buildClientSearchDisplayLabel(saved),
      clientProfile: wireProfile,
      threadId,
    });
  };

  const loanOptions: { value: LoanType; label: string }[] = [
    { value: '', label: t('clientProfile.loan_undefined') },
    { value: 'INFONAVIT', label: 'INFONAVIT' },
    { value: 'FOVISSSTE', label: 'FOVISSSTE' },
    { value: 'bancario', label: t('clientProfile.loan_bank') },
    { value: 'cofinanciamiento', label: t('clientProfile.loan_cofinancing') },
    { value: 'contado', label: t('clientProfile.loan_cash') },
  ];

  const stateOptions: { value: '' | 'CDMX' | 'Morelos'; label: string }[] = [
    { value: '', label: t('clientProfile.state_any') },
    { value: 'CDMX', label: 'CDMX' },
    { value: 'Morelos', label: 'Morelos' },
  ];

  const togglePropertyTypeOption = (type: PropertyType) => {
    setProfile((prev) => ({
      ...prev,
      propertyTypes: togglePropertyType(prev.propertyTypes ?? [], type),
    }));
  };

  const propertyTypeLabel = (type: PropertyType): string => {
    const labels: Record<PropertyType, string> = {
      departamento: t('clientProfile.type_apartment'),
      casa: t('clientProfile.type_house'),
      loft: 'Loft',
      estudio: t('clientProfile.type_studio'),
      penthouse: 'Penthouse',
    };
    return labels[type];
  };

  const typeOptions: { value: PropertyType; label: string }[] =
    CLIENT_PROFILE_PROPERTY_TYPES.map((value) => ({
      value,
      label: propertyTypeLabel(value),
    }));

  const preapprovalOptions: { value: PreapprovalStatus; label: string }[] = [
    { value: '', label: t('clientProfile.preapproval_pending') },
    { value: 'documentos solicitados', label: t('clientProfile.preapproval_docs') },
    { value: 'en revisión', label: t('clientProfile.preapproval_review') },
    { value: 'pre-aprobado', label: t('clientProfile.preapproval_approved') },
    { value: 'requiere ajustes', label: t('clientProfile.preapproval_adjust') },
  ];

  return (
    <SafeAreaView style={styles.root} testID="screen-client-profile">
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.title}>{t('clientProfile.title')}</Text>
          {activeThread ? (
            <Text style={styles.threadBadge}>{activeThread.label}</Text>
          ) : null}
          <Text style={styles.subtitle}>{t('clientProfile.subtitle')}</Text>

          <Field label={t('clientProfile.client_name')}>
            <TextInput
              style={styles.input}
              value={profile.clientName}
              onChangeText={(clientName) => updateProfile({ clientName })}
              placeholder={t('clientProfile.client_name_ph')}
              placeholderTextColor={colors.textMuted}
              testID="client-profile-name"
            />
          </Field>

          <Field label={t('clientProfile.listing_mode')}>
            <Text style={styles.fieldHint}>{t('clientProfile.listing_mode_hint')}</Text>
            <View style={styles.featureRow}>
              {([
                { value: 'sale' as ListingMode, label: t('clientProfile.listing_mode_sale') },
                { value: 'rent' as ListingMode, label: t('clientProfile.listing_mode_rent') },
              ]).map(({ value, label }) => {
                const selected = profile.listingMode === value;
                return (
                  <Pressable
                    key={value}
                    style={[styles.featurePill, selected && styles.featurePillActive]}
                    onPress={() => updateProfile({
                      listingMode: selected ? '' : value,
                    })}
                    accessibilityRole="radio"
                    accessibilityState={{ selected }}
                    testID={`client-profile-mode-${value}`}
                  >
                    <Text style={[styles.featureLabel, selected && styles.featureLabelActive]}>
                      {label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </Field>

          <View style={styles.row}>
            <View style={styles.half}>
              <Field label={
                profile.listingMode === 'rent'
                  ? t('clientProfile.budget_rent')
                  : t('clientProfile.budget')
              }>
                <TextInput
                  style={styles.input}
                  value={profile.budgetMxn}
                  onChangeText={(budgetMxn) => updateProfile({ budgetMxn })}
                  placeholder={
                    profile.listingMode === 'rent'
                      ? t('clientProfile.budget_rent_ph')
                      : t('clientProfile.budget_ph')
                  }
                  placeholderTextColor={colors.textMuted}
                  keyboardType="numeric"
                  testID="client-profile-budget"
                />
              </Field>
            </View>
            {profile.listingMode !== 'rent' ? (
            <View style={styles.half}>
              <Field label={t('clientProfile.property_value')}>
                <TextInput
                  style={styles.input}
                  value={profile.propertyValueMxn}
                  onChangeText={(propertyValueMxn) => updateProfile({ propertyValueMxn })}
                  placeholder={t('clientProfile.property_value_ph')}
                  placeholderTextColor={colors.textMuted}
                  keyboardType="numeric"
                  testID="client-profile-value"
                />
              </Field>
            </View>
            ) : null}
          </View>

          {profile.listingMode !== 'rent' ? (
          <FormSelect
            label={t('clientProfile.loan_type')}
            value={profile.loanType}
            options={loanOptions}
            onChange={onLoanChange}
            testID="client-profile-loan"
          />
          ) : null}

          <FormSelect
            label={t('clientProfile.state')}
            value={profile.state}
            options={stateOptions}
            onChange={(state) => updateProfile({ state })}
            testID="client-profile-state"
          />

          <Field label={t('clientProfile.area')}>
            <TextInput
              style={styles.input}
              value={profile.area}
              onChangeText={(area) => updateProfile({ area })}
              placeholder={t('clientProfile.area_ph')}
              placeholderTextColor={colors.textMuted}
              testID="client-profile-area"
            />
          </Field>

          <Field label={t('clientProfile.property_type')}>
            <Text style={styles.fieldHint}>{t('clientProfile.property_type_hint')}</Text>
            <View style={styles.featureRow}>
              {typeOptions.map(({ value, label }) => {
                const selected = (profile.propertyTypes ?? []).includes(value);
                return (
                  <Pressable
                    key={value}
                    style={[styles.featurePill, selected && styles.featurePillActive]}
                    onPress={() => togglePropertyTypeOption(value)}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: selected }}
                    testID={`client-profile-type-${value}`}
                  >
                    <Text style={[styles.featureLabel, selected && styles.featureLabelActive]}>
                      {label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </Field>

          <View style={styles.row}>
            <View style={styles.half}>
              <Field label={t('clientProfile.beds')}>
                <TextInput
                  style={styles.input}
                  value={profile.beds}
                  onChangeText={(beds) => updateProfile({ beds })}
                  placeholder="2"
                  placeholderTextColor={colors.textMuted}
                  keyboardType="numeric"
                  testID="client-profile-beds"
                />
              </Field>
            </View>
            <View style={styles.half}>
              <Field label={t('clientProfile.baths')}>
                <TextInput
                  style={styles.input}
                  value={profile.baths}
                  onChangeText={(baths) => updateProfile({ baths })}
                  placeholder="2"
                  placeholderTextColor={colors.textMuted}
                  keyboardType="numeric"
                  testID="client-profile-baths"
                />
              </Field>
            </View>
          </View>

          {profile.listingMode !== 'rent' ? (
            <ClientProfileBuyPanels
              profile={profile}
              analysis={analysis}
              onUpdate={updateProfile}
              preapprovalOptions={preapprovalOptions}
            />
          ) : null}

          <Field label={t('clientProfile.features')}>
            <View style={styles.featureRow}>
              {CLIENT_PROFILE_FEATURES.map((feature) => {
                const selected = profile.features.includes(feature);
                return (
                  <Pressable
                    key={feature}
                    style={[styles.featurePill, selected && styles.featurePillActive]}
                    onPress={() => toggleFeature(feature)}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: selected }}
                    testID={`client-profile-feature-${feature.replace(/\s+/g, '-')}`}
                  >
                    <Text style={[styles.featureLabel, selected && styles.featureLabelActive]}>
                      {feature}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </Field>

          <Field label={t('clientProfile.notes')}>
            <TextInput
              style={styles.textArea}
              multiline
              value={profile.notes}
              onChangeText={(notes) => updateProfile({ notes })}
              placeholder={t('clientProfile.notes_ph')}
              placeholderTextColor={colors.textMuted}
              textAlignVertical="top"
              testID="client-profile-notes"
            />
          </Field>

          <View style={styles.actions}>
            <Pressable
              style={[styles.btnPrimary, busy && styles.btnDisabled]}
              onPress={() => { void onSubmit(); }}
              disabled={busy}
              accessibilityRole="button"
              testID="client-profile-submit"
            >
              {busy ? (
                <ActivityIndicator color={colors.navy} size="small" />
              ) : (
                <Text style={styles.btnPrimaryLabel}>{t('clientProfile.submit')}</Text>
              )}
            </Pressable>
            <Pressable
              style={[styles.btnSecondary, busy && styles.btnDisabled]}
              onPress={onClear}
              disabled={busy}
              accessibilityRole="button"
              testID="client-profile-clear"
            >
              <Text style={styles.btnSecondaryLabel}>{t('clientProfile.clear')}</Text>
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.navy },
  flex: { flex: 1 },
  scroll: { padding: spacing.xl, gap: spacing.m, paddingBottom: spacing.xxxl },
  title: { ...typography.h2, color: colors.textPrimary },
  threadBadge: {
    ...typography.caption,
    color: colors.agentLocator,
    fontWeight: '600',
    marginTop: spacing.xs,
  },
  subtitle: { ...typography.body, color: colors.textSecondary },
  field: { gap: spacing.xs },
  fieldLabel: { ...typography.caption, color: colors.textSecondary },
  fieldHint: { ...typography.caption, color: colors.textMuted, marginBottom: spacing.xs },
  input: {
    padding: spacing.m,
    borderRadius: radii.s,
    borderWidth: 1,
    borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
    color: colors.textPrimary,
    ...typography.body,
  },
  textArea: {
    minHeight: 88,
    padding: spacing.m,
    borderRadius: radii.s,
    borderWidth: 1,
    borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
    color: colors.textPrimary,
    ...typography.body,
  },
  row: { flexDirection: 'row', gap: spacing.m },
  half: { flex: 1 },
  third: { flex: 1 },
  panel: {
    padding: spacing.m,
    borderRadius: radii.m,
    backgroundColor: colors.navyEl2,
    borderWidth: 1,
    borderColor: colors.hairline,
    gap: spacing.m,
  },
  panelTitle: { ...typography.bodyBold, color: colors.textPrimary },
  panelHint: { ...typography.caption, color: colors.textMuted },
  budgetGrid: { gap: spacing.s },
  budgetRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: spacing.m,
    paddingVertical: spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline,
  },
  budgetLabel: { ...typography.caption, color: colors.textSecondary, flex: 1 },
  budgetValue: { ...typography.body, color: colors.textPrimary },
  pending: { ...typography.caption, color: colors.textMuted },
  checkRow: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.m },
  checkbox: {
    width: 22,
    height: 22,
    borderRadius: radii.s,
    borderWidth: 1,
    borderColor: colors.hairline,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 2,
  },
  checkboxOn: { backgroundColor: colors.gold, borderColor: colors.gold },
  checkMark: { color: colors.navy, fontWeight: '700', fontSize: 14 },
  checkLabel: { ...typography.body, color: colors.textPrimary, flex: 1 },
  featureRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.s },
  featurePill: {
    paddingVertical: spacing.s,
    paddingHorizontal: spacing.m,
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: colors.hairline,
    backgroundColor: colors.navyEl1,
  },
  featurePillActive: { borderColor: colors.agentLocator, backgroundColor: colors.navy },
  featureLabel: { ...typography.caption, color: colors.textSecondary },
  featureLabelActive: { color: colors.agentLocator },
  actions: { gap: spacing.m, marginTop: spacing.m },
  btnPrimary: {
    paddingVertical: spacing.m,
    paddingHorizontal: spacing.l,
    borderRadius: radii.pill,
    alignItems: 'center',
    backgroundColor: colors.agentLocator,
  },
  btnPrimaryLabel: { ...typography.bodyBold, color: colors.navy },
  btnSecondary: {
    paddingVertical: spacing.m,
    paddingHorizontal: spacing.l,
    borderRadius: radii.pill,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.hairline,
  },
  btnSecondaryLabel: { ...typography.body, color: colors.textSecondary },
  btnDisabled: { opacity: 0.5 },
});
