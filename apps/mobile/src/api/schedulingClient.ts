/**
 * Scheduling service client — propose + confirm visit slots (MVP parity).
 */
const SCHEDULING_BASE =
  (process.env.EXPO_PUBLIC_SCHEDULING_URL as string | undefined)
  ?? 'http://localhost:8080/api/scheduling';

export interface VisitSlot {
  start: string;
  end: string;
}

export interface ProposeVisitResponse {
  visit_id: string;
  slots: VisitSlot[];
}

export interface ConfirmVisitResponse {
  id: string;
  listing_id: string;
  buyer_id: string;
  status: string;
  slots: VisitSlot[];
  selected_slot: VisitSlot | null;
}

function baseUrl(): string {
  return SCHEDULING_BASE.replace(/\/$/, '');
}

export async function proposeVisit(
  listingId: string,
  buyerId: string,
): Promise<ProposeVisitResponse | null> {
  try {
    const resp = await fetch(`${baseUrl()}/schedule/propose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ listing_id: listingId, buyer_id: buyerId }),
    });
    if (!resp.ok) return null;
    return (await resp.json()) as ProposeVisitResponse;
  } catch {
    return null;
  }
}

export async function confirmVisit(
  visitId: string,
  slotIndex: number,
): Promise<ConfirmVisitResponse | null> {
  try {
    const resp = await fetch(`${baseUrl()}/schedule/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ visit_id: visitId, slot_index: slotIndex }),
    });
    if (!resp.ok) return null;
    return (await resp.json()) as ConfirmVisitResponse;
  } catch {
    return null;
  }
}
