import {
  applyLoanDefaults,
  budgetAnalysis,
  buildClientSearchPrompt,
  buildClientSearchDisplayLabel,
  emptyClientProfile,
  loanDefaults,
  isClientProfileFilled,
  summarizeClientProfile,
  normalizeClientProfile,
  clientProfileToWire,
} from './clientProfile';
import type { ClientProfileDraft } from './clientProfile';

describe('clientProfile', () => {
  it('applies loan defaults like MVP buyer.html', () => {
    expect(loanDefaults('bancario')).toEqual({ notary: 6, down: 20, closing: 3 });
    expect(loanDefaults('INFONAVIT')).toEqual({ notary: 5, down: 0, closing: 2 });
    const updated = applyLoanDefaults({ ...emptyClientProfile(), loanType: 'contado' });
    expect(updated.notaryRate).toBe('6');
    expect(updated.downRate).toBe('0');
    expect(updated.closingRate).toBe('0');
  });

  it('computes budget analysis from property value', () => {
    const profile = {
      ...emptyClientProfile(),
      propertyValueMxn: '8000000',
      notaryRate: '6',
      downRate: '10',
      closingRate: '3',
    };
    const a = budgetAnalysis(profile);
    expect(a.propertyValue).toBe(8000000);
    expect(a.notaryFees).toBe(480000);
    expect(a.downPayment).toBe(800000);
    expect(a.closingCosts).toBe(240000);
    expect(a.upfrontCash).toBe(1520000);
    expect(a.financedAmount).toBe(7200000);
  });

  it('parses es-MX and US thousand separators in budget fields', () => {
    expect(clientProfileToWire({
      ...emptyClientProfile(),
      budgetMxn: '8.000.000',
      area: 'Condesa',
    })?.budget_mxn).toBe(8_000_000);
    expect(clientProfileToWire({
      ...emptyClientProfile(),
      budgetMxn: '8,000,000',
      area: 'Condesa',
    })?.budget_mxn).toBe(8_000_000);
    expect(clientProfileToWire({
      ...emptyClientProfile(),
      budgetMxn: '$8,000,000 MXN',
      area: 'Condesa',
    })?.budget_mxn).toBe(8_000_000);
  });

  it('includes listing mode in prompt, wire payload, and summary chips', () => {
    const rentProfile: ClientProfileDraft = {
      ...emptyClientProfile(),
      listingMode: 'rent',
      budgetMxn: '35000',
      area: 'Roma Norte',
      state: 'CDMX',
      propertyTypes: ['departamento'],
    };
    const prompt = buildClientSearchPrompt(rentProfile);
    expect(prompt).toContain('en renta anual');
    expect(prompt).toContain('hasta 35,000 pesos al mes');
    expect(clientProfileToWire(rentProfile)?.listing_mode).toBe('rent');
    expect(summarizeClientProfile(rentProfile)).toContain('Renta');

    const saleProfile: ClientProfileDraft = {
      ...emptyClientProfile(),
      listingMode: 'sale',
      budgetMxn: '8000000',
    };
    expect(buildClientSearchPrompt(saleProfile)).toContain('en venta');
    expect(summarizeClientProfile(saleProfile)).toContain('Compra');
  });

  it('builds MVP-style search prompt with credit follow-up', () => {
    const profile: ClientProfileDraft = {
      ...emptyClientProfile(),
      clientName: 'María González',
      budgetMxn: '8000000',
      propertyValueMxn: '7600000',
      loanType: 'bancario' as const,
      state: 'CDMX' as const,
      area: 'Condesa',
      propertyTypes: ['departamento'],
      beds: '2',
      baths: '2',
      notaryRate: '6',
      downRate: '20',
      closingRate: '3',
      creditBroker: 'Gerardo Hernández',
      preapprovalStatus: 'documentos solicitados' as const,
      consumerAgreement: true,
      features: ['terraza', 'pet friendly'],
      notes: 'Cerca de transporte',
    };
    const prompt = buildClientSearchPrompt(profile);
    expect(prompt).toContain('Busco propiedades para un cliente');
    expect(prompt).toContain('llamado María González');
    expect(prompt).toContain('que busca departamento');
    expect(prompt).toContain('en Condesa, CDMX');
    expect(prompt).toContain('con 2 recámaras');
    expect(prompt).toContain('hasta 8,000,000 pesos');
    expect(prompt).toContain('Análisis de presupuesto');
    expect(prompt).toContain('broker de crédito: Gerardo Hernández');
    expect(prompt).toContain('estatus de pre-aprobación: documentos solicitados');
    expect(prompt).toContain('autorizado para seguimiento crediticio');
    expect(prompt).toContain('con características: terraza, pet friendly');
    expect(prompt).toContain('Notas adicionales: Cerca de transporte');
  });

  it('supports multiple property types in prompt and wire payload', () => {
    const profile: ClientProfileDraft = {
      ...emptyClientProfile(),
      propertyTypes: ['departamento', 'casa'],
      area: 'Condesa',
    };
    const prompt = buildClientSearchPrompt(profile);
    expect(prompt).toContain('que busca departamento o casa');
    const wire = clientProfileToWire(profile);
    expect(wire?.property_types).toEqual(['departamento', 'casa']);
  });

  it('builds a short chat label separate from the orchestrator prompt', () => {
    const profile: ClientProfileDraft = {
      ...emptyClientProfile(),
      clientName: 'María González',
      budgetMxn: '8000000',
      area: 'Condesa',
      state: 'CDMX' as const,
      propertyTypes: ['departamento'],
    };
    const prompt = buildClientSearchPrompt(profile);
    const label = buildClientSearchDisplayLabel(profile);
    expect(prompt.length).toBeGreaterThan(label.length);
    expect(label).toBe('Buscar propiedades para María González');
    expect(prompt).toContain('hasta 8,000,000 pesos');
  });

  it('migrates legacy single propertyType field', () => {
    const normalized = normalizeClientProfile({
      ...emptyClientProfile(),
      propertyType: 'loft',
    } as Partial<ClientProfileDraft> & { propertyType: string });
    expect(normalized.propertyTypes).toEqual(['loft']);
  });

  it('summarizes legacy profiles missing propertyTypes', () => {
    const chips = summarizeClientProfile({
      ...emptyClientProfile(),
      clientName: 'Ana',
      area: 'Condesa',
    } as ClientProfileDraft);
    expect(chips).toContain('Ana');
    expect(chips).toContain('Condesa');
  });

  it('detects filled profiles and builds wire payload', () => {
    const empty = emptyClientProfile();
    expect(isClientProfileFilled(empty)).toBe(false);
    expect(clientProfileToWire(empty)).toBeNull();

    const filled = { ...empty, area: 'Condesa', budgetMxn: '8000000' };
    expect(isClientProfileFilled(filled)).toBe(true);
    expect(summarizeClientProfile(filled)).toContain('Condesa');
    const wire = clientProfileToWire(filled);
    expect(wire?.budget_mxn).toBe(8000000);
    expect(wire?.area).toBe('Condesa');
  });
});
