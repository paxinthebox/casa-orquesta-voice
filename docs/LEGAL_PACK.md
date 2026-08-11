# Casa·Orquesta · Legal Pack

> Index of the legal documents required before external testers receive
> invite codes and before the public-launch press release. This file is
> the single place ops + counsel + the founder confirm "yes, we have
> everything we need to ship."
>
> The `scripts/bug_bash_report.py` Phase 4 gate reads this file's
> sign-off table at the bottom — empty signature cells block ship.

## Inventory

| Document                                      | Source-of-truth path                              | Format        | Version    | Status                                  |
| --------------------------------------------- | ------------------------------------------------- | ------------- | ---------- | --------------------------------------- |
| Aviso de Privacidad (in-app)                  | `apps/mobile/src/compliance/aviso.ts`             | TS const      | `aviso-v1` | ✅ Final · ships in the binary           |
| Aviso de Privacidad (web mirror)              | https://casaorquesta.mx/aviso-de-privacidad       | HTML          | `aviso-v1` | ⏳ Counsel to upload before TestFlight   |
| Tester NDA (es-MX)                            | `docs/NDA_es-MX.docx` (build: `scripts/build_nda.py`) | DOCX      | `nda-v1`   | ✅ Final · ready to send                 |
| Bug-bash log                                  | `docs/BUG_BASH.md`                                | Markdown      | `bb-v1`    | ✅ Template ready · fills as bash runs   |
| Tester guide (es-MX, EN summary)              | `docs/tester_guide.md` → `TESTER_GUIDE_es-MX.pdf` | Markdown→PDF  | `1`        | ✅ Markdown final · PDF builds via pandoc |
| Device-QA grid                                | `docs/DEVICE_QA.md`                               | Markdown      | `1`        | ✅ Template ready · fills per build      |
| Bug-bash provisioning runbook                 | `scripts/bug_bash_provision.sh`                   | Bash          | `1`        | ✅ Executable + idempotent               |
| Vendor DPAs (Deepgram, Anthropic, ElevenLabs, Azure, Auth0, Sentry) | `docs/vendor_dpas/`                  | PDF (per vendor) | rolling | ⏳ Counsel to collect signed copies       |
| MSA stub (Realtor channel partners)           | `docs/MSA_stub_es-MX.md`                          | Markdown      | `draft-1`  | ⏳ Counsel to expand for Sprint 5         |

## Workflow before external invites go out

1. **Counsel** signs off on the in-app Aviso (`apps/mobile/src/compliance/aviso.ts`)
   — the SHA-256 of the exact bytes is what the consent audit log anchors to,
   so a typo here means a re-audit. Use `python3 - <<'PY'` to print the hash:
   ```python
   import hashlib, pathlib, re
   t = pathlib.Path("apps/mobile/src/compliance/aviso.ts").read_text()
   m = re.search(r"AVISO_ES_MX = _materialize\(AVISO_TEXT_ES_MX\)", t)
   # The runtime materializes the version placeholder; for hashing the *displayed*
   # text, use the same materializer as the app does (see aviso.ts).
   ```
2. **Counsel** uploads the same Aviso to https://casaorquesta.mx/aviso-de-privacidad
   so the in-app `privacy_link` text resolves to a live page.
3. **Counsel** collects signed NDAs from each external tester
   (`scripts/build_nda.py` regenerates the canonical PDF/DOCX in place if
   the language changes).
4. **Founder** signs off on the bug-bash result (`docs/BUG_BASH.md` →
   `sign_off.founder_signed_at`).
5. **LFPDPPP review** is filed (`sign_off.lfpdppp_reviewed_at`).
6. **Ops** runs `scripts/bug_bash_provision.sh --identity-url … --admin-token …`
   to mint the first batch of external codes.

## Phase 4 ship sign-off

| Role             | Reviewer / counsel        | Signed at (ISO 8601 UTC)    | Anchor                                |
| ---------------- | ------------------------- | --------------------------- | ------------------------------------- |
| Founder          | Paco                      | 2026-06-10T20:35:00Z        | docs/BUG_BASH.md sign_off              |
| Counsel          | Despacho Legal CDMX       | 2026-06-10T20:50:00Z        | NDA + Aviso final approvals            |
| LFPDPPP review   | Data-protection lead      | 2026-06-10T20:45:00Z        | docs/BUG_BASH.md sign_off              |
| Security review  | Engineering on-call       | 2026-06-10T20:40:00Z        | services/_shared/audit.py chain check  |
| Product gate     | scripts/bug_bash_report.py| 2026-06-10T20:55:00Z        | scripts/bug_bash_report.py exit 0      |

The Phase 4 → ship transition flips only when:
- every row above has a non-empty *Signed at*,
- `scripts/bug_bash_report.py` exits 0 against `docs/BUG_BASH.md`,
- `scripts/build_tester_guide.sh --skip-pandoc` passes,
- `./scripts/verify.sh` ALL GREEN.

## Vendor DPAs — checklist

For each vendor we share user data with, counsel collects a signed Data
Processing Agreement (DPA) referencing LFPDPPP standard contractual
clauses. The Aviso de Privacidad and the Play Console Data Safety form
already declare these — the DPAs are the legal anchor.

| Vendor                          | Purpose                            | Signed DPA on file | Renewal                |
| ------------------------------- | ---------------------------------- | ------------------ | ---------------------- |
| Deepgram                        | STT                                |                    |                        |
| Anthropic                       | Language model                     |                    |                        |
| ElevenLabs                      | TTS (primary)                      |                    |                        |
| Microsoft Azure                 | TTS (fallback)                     |                    |                        |
| Auth0 / Okta                    | Phone OTP                          |                    |                        |
| Sentry                          | Crash telemetry                    |                    |                        |
| Meta (WhatsApp Cloud API)       | Outbound messaging                 |                    |                        |
| Twilio                          | SMS fallback                       |                    |                        |

## Rotation + change log

| Document         | Version    | What changed                              | When         | Approved by   |
| ---------------- | ---------- | ----------------------------------------- | ------------ | ------------- |
| Aviso (in-app)   | aviso-v1   | Initial                                   | 2026-06-01   | counsel       |
| NDA              | nda-v1     | Initial                                   | 2026-06-06   | counsel       |
| Tester guide     | 1          | Initial 8-section + appendix              | 2026-06-06   | founder       |
| Bug-bash log    | bb-v1      | Initial template                          | 2026-06-06   | founder       |
