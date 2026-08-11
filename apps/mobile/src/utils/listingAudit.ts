/**
 * One-tap listing audit prompt — matches orchestrator audit routing (test_agents §7).
 */
export function buildListingAuditPrompt(locale: string): string {
  if (locale.startsWith('en')) {
    return (
      'Audit this property: check RPP for encumbrances, Catastro for property tax, and INEGI zone stats.'
    );
  }
  return 'Audita esta propiedad: revisa RPP por gravámenes, Catastro por predial, e INEGI.';
}
