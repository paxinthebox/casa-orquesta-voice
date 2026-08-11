/**
 * MVP buyer.html parity — client intake form + budget analysis + search prompt.
 */

export type LoanType =
  | ''
  | 'INFONAVIT'
  | 'FOVISSSTE'
  | 'bancario'
  | 'cofinanciamiento'
  | 'contado';

export type PropertyType =
  | 'departamento'
  | 'casa'
  | 'loft'
  | 'estudio'
  | 'penthouse';

export const CLIENT_PROFILE_PROPERTY_TYPES: readonly PropertyType[] = [
  'departamento',
  'casa',
  'loft',
  'estudio',
  'penthouse',
] as const;

export type PreapprovalStatus =
  | ''
  | 'documentos solicitados'
  | 'en revisión'
  | 'pre-aprobado'
  | 'requiere ajustes';

/** Sale (buy) vs long-term annual rent — drives matching `listing_mode`. */
export type ListingMode = '' | 'sale' | 'rent';

export interface ClientProfileDraft {
  clientName: string;
  listingMode: ListingMode;
  budgetMxn: string;
  propertyValueMxn: string;
  loanType: LoanType;
  state: '' | 'CDMX' | 'Morelos';
  area: string;
  propertyTypes: PropertyType[];
  beds: string;
  baths: string;
  notaryRate: string;
  downRate: string;
  closingRate: string;
  creditBroker: string;
  creditBrokerContact: string;
  preapprovalStatus: PreapprovalStatus;
  preapprovalDate: string;
  preapprovalNotes: string;
  consumerAgreement: boolean;
  features: string[];
  notes: string;
}

export interface BudgetAnalysis {
  propertyValue: number;
  notaryRate: number;
  downRate: number;
  closingRate: number;
  notaryFees: number;
  downPayment: number;
  closingCosts: number;
  upfrontCash: number;
  totalAcquisitionCost: number;
  financedAmount: number;
}

export const CLIENT_PROFILE_FEATURES = [
  'alberca',
  'jardín',
  'pet friendly',
  'terraza',
  'rooftop',
  'parking',
  'amueblado',
  'vista',
  'elevador',
  'seguridad',
] as const;

/** Stable empty profile for selectors — never mutate this object. */
export const EMPTY_CLIENT_PROFILE: Readonly<ClientProfileDraft> = Object.freeze({
  clientName: '',
  listingMode: '' as ListingMode,
  budgetMxn: '',
  propertyValueMxn: '',
  loanType: '' as LoanType,
  state: '' as ClientProfileDraft['state'],
  area: '',
  propertyTypes: [] as PropertyType[],
  beds: '',
  baths: '',
  notaryRate: '6',
  downRate: '10',
  closingRate: '3',
  creditBroker: '',
  creditBrokerContact: '',
  preapprovalStatus: '' as PreapprovalStatus,
  preapprovalDate: '',
  preapprovalNotes: '',
  consumerAgreement: false,
  features: [] as string[],
  notes: '',
});

export function emptyClientProfile(): ClientProfileDraft {
  return {
    ...EMPTY_CLIENT_PROFILE,
    features: [],
    propertyTypes: [],
  };
}

const VALID_PROPERTY_TYPES = new Set<string>(CLIENT_PROFILE_PROPERTY_TYPES);

function normalizeListingMode(raw: unknown): ListingMode {
  if (raw === 'rent' || raw === 'sale') return raw;
  return '';
}

/** Normalize persisted profiles (legacy single `propertyType` → `propertyTypes[]`). */
export function normalizeClientProfile(
  raw: Partial<ClientProfileDraft> & { propertyType?: string },
): ClientProfileDraft {
  const base = emptyClientProfile();
  const merged = { ...base, ...raw, features: [...(raw.features ?? base.features)] };

  let propertyTypes = Array.isArray(raw.propertyTypes)
    ? raw.propertyTypes.filter((t): t is PropertyType => VALID_PROPERTY_TYPES.has(t))
    : [];
  if (!propertyTypes.length && typeof raw.propertyType === 'string' && raw.propertyType.trim()) {
    const legacy = raw.propertyType.trim();
    if (VALID_PROPERTY_TYPES.has(legacy)) {
      propertyTypes = [legacy as PropertyType];
    }
  }

  return {
    ...merged,
    propertyTypes,
    listingMode: normalizeListingMode(raw.listingMode),
  };
}

export function togglePropertyType(
  current: PropertyType[],
  type: PropertyType,
): PropertyType[] {
  return current.includes(type)
    ? current.filter((t) => t !== type)
    : [...current, type];
}

export function loanDefaults(loan: LoanType): { notary: number; down: number; closing: number } {
  const rules: Record<LoanType, { notary: number; down: number; closing: number }> = {
    '': { notary: 6, down: 10, closing: 3 },
    INFONAVIT: { notary: 5, down: 0, closing: 2 },
    FOVISSSTE: { notary: 5, down: 0, closing: 2 },
    bancario: { notary: 6, down: 20, closing: 3 },
    cofinanciamiento: { notary: 6, down: 10, closing: 3 },
    contado: { notary: 6, down: 0, closing: 0 },
  };
  return rules[loan] ?? rules[''];
}

export function applyLoanDefaults(profile: ClientProfileDraft): ClientProfileDraft {
  const rules = loanDefaults(profile.loanType);
  return {
    ...profile,
    notaryRate: String(rules.notary),
    downRate: String(rules.down),
    closingRate: String(rules.closing),
  };
}

/** Parse money / counts from free-typed profile fields (es-MX or US separators). */
export function parseNumber(raw: string): number {
  let s = raw.trim().replace(/[$\s]|MXN|mxn/gi, '');
  if (!s) return 0;

  // es-MX thousands: 8.000.000 or 8.000.000,50
  if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) {
    s = s.replace(/\./g, '').replace(',', '.');
  } else if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) {
    // US thousands: 8,000,000 or 8,000,000.50
    s = s.replace(/,/g, '');
  } else if (s.includes(',') && !s.includes('.')) {
    // Ambiguous "8000,5" decimal or lone thousands comma
    const parts = s.split(',');
    if (parts.length === 2 && parts[1].length <= 2) {
      s = `${parts[0]}.${parts[1]}`;
    } else {
      s = s.replace(/,/g, '');
    }
  } else {
    s = s.replace(/,/g, '');
  }

  const n = Number(s);
  return Number.isFinite(n) ? n : 0;
}

export function budgetAnalysis(profile: ClientProfileDraft): BudgetAnalysis {
  const budget = parseNumber(profile.budgetMxn);
  const propertyValue = parseNumber(profile.propertyValueMxn) || budget || 0;
  const notaryRate = parseNumber(profile.notaryRate);
  const downRate = parseNumber(profile.downRate);
  const closingRate = parseNumber(profile.closingRate);
  const notaryFees = propertyValue * notaryRate / 100;
  const downPayment = propertyValue * downRate / 100;
  const closingCosts = propertyValue * closingRate / 100;
  const upfrontCash = notaryFees + downPayment + closingCosts;
  const totalAcquisitionCost = propertyValue + notaryFees + closingCosts;
  const financedAmount = Math.max(0, propertyValue - downPayment);
  return {
    propertyValue,
    notaryRate,
    downRate,
    closingRate,
    notaryFees,
    downPayment,
    closingCosts,
    upfrontCash,
    totalAcquisitionCost,
    financedAmount,
  };
}

export function formatMoneyMxn(n: number): string {
  return `$${Math.round(n || 0).toLocaleString('es-MX')} MXN`;
}

/** Mirrors MVP submitCustomerProfile() → quickSend(...). */
export function buildClientSearchPrompt(profile: ClientProfileDraft): string {
  const analysis = budgetAnalysis(profile);
  const parts = ['Busco propiedades para un cliente'];

  const name = profile.clientName.trim();
  const budget = profile.budgetMxn.trim();
  const loan = profile.loanType.trim();
  const state = profile.state.trim();
  const area = profile.area.trim();
  const typeLabels = profile.propertyTypes.map((t) => t).join(' o ');
  const beds = profile.beds.trim();
  const baths = profile.baths.trim();
  const creditBroker = profile.creditBroker.trim();
  const creditBrokerContact = profile.creditBrokerContact.trim();
  const preapprovalStatus = profile.preapprovalStatus.trim();
  const preapprovalDate = profile.preapprovalDate.trim();
  const preapprovalNotes = profile.preapprovalNotes.trim();
  const notes = profile.notes.trim();

  if (name) parts.push(`llamado ${name}`);
  if (profile.listingMode === 'rent') parts.push('en renta anual');
  else if (profile.listingMode === 'sale') parts.push('en venta');
  if (typeLabels) parts.push(`que busca ${typeLabels}`);
  if (area || state) parts.push(`en ${[area, state].filter(Boolean).join(', ')}`);
  if (beds) parts.push(`con ${beds} recámaras`);
  if (baths) parts.push(`y mínimo ${baths} baños`);
  const budgetNum = parseNumber(budget);
  if (budgetNum > 0) {
    const amount = budgetNum.toLocaleString('es-MX');
    if (profile.listingMode === 'rent') {
      parts.push(`hasta ${amount} pesos al mes`);
    } else {
      parts.push(`hasta ${amount} pesos`);
    }
  }
  if (loan && profile.listingMode !== 'rent') parts.push(`con pago o crédito ${loan}`);

  if (analysis.propertyValue && profile.listingMode !== 'rent') {
    parts.push(
      `Análisis de presupuesto: valor de propiedad ${formatMoneyMxn(analysis.propertyValue)}, `
      + `notaría ${formatMoneyMxn(analysis.notaryFees)}, enganche ${formatMoneyMxn(analysis.downPayment)}, `
      + `costos de cierre ${formatMoneyMxn(analysis.closingCosts)}, `
      + `efectivo para cierre estimado ${formatMoneyMxn(analysis.upfrontCash)}, `
      + `costo total de operación ${formatMoneyMxn(analysis.totalAcquisitionCost)}, `
      + `monto financiado estimado ${formatMoneyMxn(analysis.financedAmount)}`,
    );
  }

  if (
    creditBroker
    || preapprovalStatus
    || preapprovalDate
    || preapprovalNotes
    || profile.consumerAgreement
  ) {
    const agreementParts: string[] = [];
    if (creditBroker) agreementParts.push(`broker de crédito: ${creditBroker}`);
    if (creditBrokerContact) agreementParts.push(`contacto del broker: ${creditBrokerContact}`);
    if (preapprovalStatus) agreementParts.push(`estatus de pre-aprobación: ${preapprovalStatus}`);
    if (preapprovalDate) agreementParts.push(`próximo seguimiento: ${preapprovalDate}`);
    if (preapprovalNotes) agreementParts.push(`notas de pre-aprobación: ${preapprovalNotes}`);
    agreementParts.push(
      `convenio del consumidor: ${profile.consumerAgreement
        ? 'autorizado para seguimiento crediticio'
        : 'pendiente de autorización'}`,
    );
    parts.push(`Seguimiento crediticio bajo convenio del consumidor: ${agreementParts.join('; ')}`);
  }

  if (profile.features.length) {
    parts.push(`con características: ${profile.features.join(', ')}`);
  }
  if (notes) parts.push(`Notas adicionales: ${notes}`);

  return `${parts.join(' ')}.`;
}

/** True when the thread has any search-shaping criteria saved. */
export function isClientProfileFilled(profile: ClientProfileDraft): boolean {
  const p = normalizeClientProfile(profile);
  return !!(
    p.clientName.trim()
    || p.budgetMxn.trim()
    || p.propertyValueMxn.trim()
    || p.listingMode
    || p.loanType
    || p.state
    || p.area.trim()
    || p.propertyTypes.length
    || p.beds.trim()
    || p.baths.trim()
    || p.features.length
    || p.notes.trim()
    || p.creditBroker.trim()
    || p.preapprovalStatus
  );
}

/** Short labels for thread chips / summary (es-MX copy in strings). */
export function summarizeClientProfile(profile: ClientProfileDraft): string[] {
  const p = normalizeClientProfile(profile);
  const chips: string[] = [];
  const name = p.clientName.trim();
  const budget = parseNumber(p.budgetMxn);
  const area = p.area.trim();
  const state = p.state.trim();

  if (name) chips.push(name);
  if (p.listingMode === 'rent') chips.push('Renta');
  else if (p.listingMode === 'sale') chips.push('Compra');
  if (budget > 0) {
    chips.push(
      p.listingMode === 'rent'
        ? `${formatMoneyMxn(budget)}/mes`
        : formatMoneyMxn(budget),
    );
  }
  if (area && state) chips.push(`${area}, ${state}`);
  else if (area) chips.push(area);
  else if (state) chips.push(state);
  if (p.propertyTypes.length) chips.push(p.propertyTypes.join(', '));
  if (p.beds.trim()) chips.push(`${p.beds} rec.`);
  if (p.baths.trim()) chips.push(`${p.baths} baños`);
  if (p.loanType) chips.push(p.loanType);
  if (p.preapprovalStatus) chips.push(p.preapprovalStatus);
  return chips;
}

/** Short label for the chat bubble — full prompt still goes to the orchestrator. */
export function buildClientSearchDisplayLabel(profile: ClientProfileDraft): string {
  const name = profile.clientName.trim();
  if (name) return `Buscar propiedades para ${name}`;
  const chips = summarizeClientProfile(profile);
  if (chips.length) {
    return `Buscar propiedades (${chips.slice(0, 3).join(' · ')})`;
  }
  return 'Buscar propiedades con perfil del cliente';
}

/** snake_case wire shape for gateway / orchestrator session state. */
export function clientProfileToWire(
  profile: ClientProfileDraft,
): Record<string, unknown> | null {
  if (!isClientProfileFilled(profile)) return null;

  const budget = parseNumber(profile.budgetMxn);
  const propertyValue = parseNumber(profile.propertyValueMxn);
  const beds = parseNumber(profile.beds);
  const baths = parseNumber(profile.baths);

  const wire: Record<string, unknown> = {};
  const name = profile.clientName.trim();
  if (name) wire.client_name = name;
  if (profile.listingMode) wire.listing_mode = profile.listingMode;
  if (budget > 0) wire.budget_mxn = budget;
  if (propertyValue > 0) wire.property_value_mxn = propertyValue;
  if (profile.loanType) wire.loan_type = profile.loanType;
  if (profile.state) wire.state = profile.state;
  const area = profile.area.trim();
  if (area) wire.area = area;
  if (profile.propertyTypes.length) wire.property_types = [...profile.propertyTypes];
  if (beds > 0) wire.beds_min = beds;
  if (baths > 0) wire.baths_min = baths;
  if (profile.features.length) wire.features = [...profile.features];
  const notes = profile.notes.trim();
  if (notes) wire.notes = notes;

  const creditBroker = profile.creditBroker.trim();
  if (creditBroker) wire.credit_broker = creditBroker;
  const creditContact = profile.creditBrokerContact.trim();
  if (creditContact) wire.credit_broker_contact = creditContact;
  if (profile.preapprovalStatus) wire.preapproval_status = profile.preapprovalStatus;
  const preDate = profile.preapprovalDate.trim();
  if (preDate) wire.preapproval_date = preDate;
  const preNotes = profile.preapprovalNotes.trim();
  if (preNotes) wire.preapproval_notes = preNotes;
  wire.consumer_agreement = profile.consumerAgreement;

  return wire;
}
