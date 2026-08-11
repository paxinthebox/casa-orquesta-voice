---
schema: bug_bash/v1
gate: phase_4
testers:
  - id: founder
    name: "Paco (founder)"
    device: "iPhone 15 Pro · iOS 18.0 · Telcel LTE (Roma Nte.)"
    code: "75TG-SFC3"
  - id: designer
    name: "Mariana (lead designer)"
    device: "Pixel 7 · Android 14 · AT&T MX LTE"
    code: "8NXR-CFPE"
  - id: advisor
    name: "Sebastián (real-estate advisor, broker)"
    device: "iPhone 13 · iOS 18.0 · Movistar MX"
    code: "28RM-4QKX"
window:
  starts_at: "2026-06-08T15:00:00-06:00"
  ends_at:   "2026-06-10T15:00:00-06:00"
sign_off:
  founder_signed_at:    "2026-06-10T14:35:00-06:00"
  counsel_signed_at:    "2026-06-10T14:50:00-06:00"
  lfpdppp_reviewed_at:  "2026-06-10T14:45:00-06:00"
---

# Casa·Orquesta · Bug Bash

> Source-of-truth for the Phase 4 decision gate (`TASK_PROMPTS.md > P4.5`):
> *Zero P0 bugs, founder signs off, LFPDPPP review clean → ship.*
>
> Three testers (founder + designer + advisor) run the full flow over 48 h.
> Every issue is logged here, classified, and either fixed before external
> invites go out (P0 + P1) or scheduled for the post-launch backlog (P2 + P3).
> The `scripts/bug_bash_report.py` tool parses this file and gates CI on
> "no open P0 + ≤ 3 open P1".

## Severity definitions

| Severity | Definition                                                    | Gate behavior                                                  |
| -------- | ------------------------------------------------------------- | -------------------------------------------------------------- |
| **P0**   | Crash, data loss, security exposure, voice loop unreachable.  | **Blocks ship.** Must be `status: fixed` or `status: wontfix` with founder waiver. |
| **P1**   | Major UX broken (mic doesn't trigger, cards don't render, etc.) | Up to 3 may be `status: open` with founder waiver. Soft-blocks otherwise. |
| **P2**   | Minor UX / cosmetic / edge-case.                              | Logged for the next sprint. Doesn't block ship.                |
| **P3**   | Nice-to-have / sugerencia.                                     | Logged. Lives in the backlog.                                  |

## Status definitions

- `open`     — needs triage or work
- `triaged`  — assigned, no PR yet
- `in_pr`    — fix has a PR; not merged
- `fixed`    — merged to main + verified by the reporter
- `wontfix`  — explicit decision not to fix; needs `waiver_by` + `waiver_reason`
- `dup`      — duplicate of another id

## Issues

| id      | severity | title                                                                       | found_by | device       | status  | fix_pr | waiver_by | waiver_reason                                                  | repro                                                                                          |
| ------- | -------- | --------------------------------------------------------------------------- | -------- | ------------ | ------- | ------ | --------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| BB-0001 | P0       | App crashes on first launch if invite already redeemed                      | founder  | iPhone 15    | fixed   | #214   |           |                                                                | Open with a redeemed code → wizard step changes mid-render → unhandled state → SIGABRT          |
| BB-0002 | P1       | Mic halo stays gold for ~250ms after first audio frame                      | designer | Pixel 7      | fixed   | #215   |           |                                                                | Hold mic, speak, watch halo: green flash should be immediate; we see ~250ms of gold first        |
| BB-0003 | P1       | Voice 'no acepto' classified as accept when phone background noise is high  | advisor  | iPhone 13    | fixed   | #216   |           |                                                                | Open ConsentModal in a noisy room → say 'no acepto' → roughly 1 in 5 times routes to accept     |
| BB-0004 | P1       | DSAR export ZIP sometimes missing payments.json on the very first request   | founder  | iPhone 15    | wontfix |        | founder   | First-request race; retry handles it. Mitigation note in app. | First-ever DSAR export after auth → payments service cold-start sometimes returns HTTP 503      |
| BB-0005 | P2       | Spanish weekday formatting capitalizes 'Lunes' inconsistently on Android    | designer | Pixel 7      | open    |        |           |                                                                | SlotCard's `toLocaleDateString('es-MX', {weekday})` lowercase on iOS, capitalized on Android    |
| BB-0006 | P3       | Add a 'how do I cancel my visit?' suggestion to onboarding welcome step     | advisor  | iPhone 13    | open    |        |           |                                                                | Several visit-cancellation questions in the first turn; surfacing the path upfront would help   |

<!--
  Severities and statuses must be in the lists above or the report script
  will exit non-zero. Empty rows (with `id: _none_`) are ignored.
  Add rows in any order; `scripts/bug_bash_report.py` validates + counts.
-->

## P0 / P1 summary (filled by `scripts/bug_bash_report.py`)

```
  P0 open / in_pr / fixed:    0 / 0 / 0
  P1 open / in_pr / fixed:    0 / 0 / 0
  P2 open / in_pr / fixed:    0 / 0 / 0
  P3 open / in_pr / fixed:    0 / 0 / 0
  open waivers (P0 + P1):     0
```

## Founder sign-off

Once every P0 is `fixed` (or formally waived) **and** the LFPDPPP review is
filed, the founder fills in `sign_off.founder_signed_at` in the YAML
front-matter. The verify gate refuses to mark Phase 4 closed until that
ISO timestamp is non-empty.

| Reviewer        | Role                | Signed at (ISO)            | Notes                  |
| --------------- | ------------------- | -------------------------- | ---------------------- |
| Founder         | Final decision      | 2026-06-10T14:35:00-06:00  | All P0 fixed; BB-0004 waived per founder authority. Bash report exit 0. |
| Counsel         | Legal review        | 2026-06-10T14:50:00-06:00  | NDA + Aviso final approvals on file. No new legal risk surfaced.        |
| LFPDPPP review  | Data-protection lead| 2026-06-10T14:45:00-06:00  | Audit chain verified clean; DSAR round-trip exercised end-to-end.       |

## Waiver log

Every `wontfix` row above MUST appear here with a one-sentence justification.
Phase 4 ship is allowed only when every waiver has a `waiver_by` reviewer.

| id      | severity | reason                                                                                                               | waiver_by | signed_at                |
| ------- | -------- | -------------------------------------------------------------------------------------------------------------------- | --------- | ------------------------ |
| BB-0004 | P1       | First-DSAR-export race window when the payments pod is cold. Retry inside the identity fan-out client already handles it; we re-export and the second attempt always succeeds. Sentry alert wired so we see if the rate creeps. Real fix is the payments warm-up pre-load, scheduled for Sprint 5. | founder   | 2026-06-10T14:30:00-06:00 |

## Post-bash retrospective

Once the bash is closed (or the sprint cycles), capture 3 bullets each for:

**What worked:**
- ...

**What didn't:**
- ...

**What we'll change next bash:**
- ...
