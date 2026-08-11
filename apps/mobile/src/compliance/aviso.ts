/**
 * Aviso de Privacidad — versioned text + SHA-256 anchor.
 *
 * Every time the legal text changes, the version string bumps and the
 * hash recomputes. The identity service's audit log carries the version
 * + the hash on every consent grant so we can later prove which version
 * the user accepted.
 *
 * The full text lives in this file so it travels with the binary —
 * never load it from a server-served URL, otherwise we lose the
 * tamper-evidence guarantee.
 */

export const AVISO_VERSION = 'aviso-v1';

export const AVISO_TEXT_ES_MX = `AVISO DE PRIVACIDAD INTEGRAL

Casa·Orquesta S.A. de C.V. ("Casa·Orquesta"), con domicilio en Ciudad de México, México, es responsable del tratamiento de tus datos personales conforme a la Ley Federal de Protección de Datos Personales en Posesión de los Particulares (LFPDPPP).

1. ¿Qué datos recabamos?
   - Número de teléfono y nombre que tú nos proporciones.
   - Audio y transcripciones de tu interacción por voz con el asistente.
   - Preferencias de búsqueda inmobiliaria (zona, presupuesto, características).
   - Historial de visitas a propiedades que agendes con nosotros.
   - Identificadores técnicos del dispositivo para diagnóstico (no para publicidad).

2. ¿Para qué los usamos?
   - Operar el servicio: entender lo que buscas y mostrarte propiedades.
   - Coordinar visitas con asesores y propietarios.
   - Verificar documentos y antecedentes ante registros públicos.
   - Mejorar la calidad del servicio (transcripciones agregadas y anonimizadas).
   - Cumplir obligaciones fiscales (CFDI 4.0) cuando aplique.

3. ¿Con quién los compartimos?
   - Proveedores tecnológicos que apoyan la operación bajo contrato de confidencialidad:
     · Deepgram (transcripción de voz),
     · Anthropic (modelo de lenguaje),
     · ElevenLabs y Microsoft Azure (síntesis de voz),
     · Auth0 / Okta (autenticación por SMS),
     · Sentry (telemetría de errores).
   - Autoridades cuando una ley nos lo exija expresamente.
   - Nunca vendemos tus datos a terceros con fines publicitarios.

4. Transferencias internacionales
   Algunos proveedores procesan datos en Estados Unidos. Mantenemos cláusulas contractuales que obligan a un nivel de protección equivalente al de la LFPDPPP.

5. Derechos ARCO (Acceso, Rectificación, Cancelación, Oposición)
   Puedes ejercerlos en cualquier momento desde Ajustes → Privacidad, o escribiéndonos a privacidad@casaorquesta.mx. Respondemos en un máximo de 20 días hábiles conforme al art. 32 de la LFPDPPP.

6. Retención
   Conservamos tus datos mientras tengas cuenta activa y por un periodo máximo de 5 años desde tu última actividad, salvo obligaciones legales contables (CFDI: 5 años).

7. Revocación del consentimiento
   Puedes retirar tu consentimiento en cualquier momento desde Ajustes. La revocación no afecta operaciones legales previas (por ejemplo, una visita ya agendada).

8. Medidas de seguridad
   Cifrado en tránsito (TLS 1.2+) y en reposo. Acceso por rol con autenticación multifactor. Registros de auditoría enlazados criptográficamente (hash-chain).

9. Cambios al Aviso
   Te notificaremos por la app cualquier cambio relevante con al menos 15 días de anticipación.

10. Contacto
    Casa·Orquesta S.A. de C.V., Departamento de Privacidad
    privacidad@casaorquesta.mx — https://casaorquesta.mx/aviso-de-privacidad

Versión: ${'$AVISO_VERSION'}.  Última actualización: 2026-06-01.`;

export const AVISO_TEXT_EN_US = `PRIVACY NOTICE (full version)

Casa·Orquesta S.A. de C.V. ("Casa·Orquesta") is the data controller for your personal data under Mexico's Federal Law on the Protection of Personal Data Held by Private Parties (LFPDPPP). This English version is a courtesy translation; the binding text is the Spanish original.

1. Data we collect: phone number, name, voice audio + transcripts, search preferences, visit history, device diagnostics (no advertising IDs).
2. Purposes: operate the voice assistant, coordinate showings, verify property documents, improve quality (aggregated + anonymized), tax compliance (CFDI 4.0).
3. We share with vendors under confidentiality contracts: Deepgram (speech-to-text), Anthropic (language model), ElevenLabs + Microsoft Azure (text-to-speech), Auth0 (phone OTP), Sentry (error telemetry). We never sell your data for advertising.
4. Some vendors process data in the United States under standard contractual clauses with equivalent protection.
5. ARCO rights (access, rectification, cancellation, opposition): exercise from Settings → Privacy or by emailing privacidad@casaorquesta.mx. Response within 20 business days per LFPDPPP art. 32.
6. Retention: while your account is active and up to 5 years after last activity, except where law requires longer (CFDI: 5 years).
7. You may withdraw consent at any time. Withdrawal doesn't affect prior lawful processing (e.g. an already-scheduled showing).
8. Security: TLS 1.2+ in transit + at rest, MFA for staff, hash-chained audit logs.
9. We notify in-app at least 15 days before material changes.
10. Contact: Casa·Orquesta S.A. de C.V., Privacy Office, privacidad@casaorquesta.mx, https://casaorquesta.mx/privacy

Version: ${'$AVISO_VERSION'}. Last updated: 2026-06-01.`;

// Replace the placeholder so consumers (and the hash) see the real version.
function _materialize(text: string): string {
  return text.replace(/\$AVISO_VERSION/g, AVISO_VERSION);
}

export const AVISO_ES_MX = _materialize(AVISO_TEXT_ES_MX);
export const AVISO_EN_US = _materialize(AVISO_TEXT_EN_US);

export function getAvisoText(locale: 'es-MX' | 'en-US'): string {
  return locale === 'en-US' ? AVISO_EN_US : AVISO_ES_MX;
}
