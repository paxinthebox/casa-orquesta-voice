/**
 * MVP buyer.html parity — default outreach copy for people follow-up.
 */
import type { PeopleCardData } from '@/state/cardsStore';

export const PERSON_AVAILABILITY_SLOTS = [
  'Hoy 16:30',
  'Mañana 10:00',
  'Mañana 13:30',
  'Viernes 11:00',
] as const;

export function personSelectionLabel(person: PeopleCardData): string {
  const kindLabels: Record<PeopleCardData['person_kind'], string> = {
    buyer: 'comprador',
    collaborator: 'agente colaborador',
    broker: 'broker',
  };
  const kind = kindLabels[person.person_kind];
  return `${kind} ${person.id} (${person.name})`;
}

export function defaultPersonMessage(person: PeopleCardData): string {
  const name = person.name;
  if (person.person_kind === 'buyer') {
    return `Hola ${name}, tengo opciones que pueden ajustarse a tu búsqueda. ¿Te comparto las mejores coincidencias?`;
  }
  if (person.person_kind === 'collaborator') {
    return `Hola ${name}, me gustaría coordinar una colaboración para una operación activa. ¿Tienes disponibilidad?`;
  }
  return `Hola ${name}, quiero coordinar seguimiento sobre una oportunidad inmobiliaria. ¿Podemos revisarla?`;
}

export function buildSendMessagePrompt(person: PeopleCardData, text: string): string {
  return `Envía este mensaje para ${personSelectionLabel(person)}: "${text.trim()}"`;
}

export function buildCallPrompt(person: PeopleCardData): string {
  return `Inicia una llamada o prepara el guion de llamada para ${personSelectionLabel(person)}.`;
}

export function buildSchedulePrompt(person: PeopleCardData, slot: string): string {
  return (
    `Programa una cita para ${personSelectionLabel(person)} en este horario disponible: ${slot}. ` +
    'Sincroniza calendarios y confirma disponibilidad.'
  );
}

export function buildSyncCalendarsPrompt(): string {
  return 'Sincroniza calendarios entre comprador, agente, broker y Casa·Orquesta.';
}
