# Casa·Orquesta · Voice — Device QA

> Source-of-truth for the Phase 3 decision gate (`TASK_PROMPTS.md > P3.4`):
> *E2E voice flow works on both a real iPhone and a real Android over Mexico LTE.*
>
> Fill in the **Result** column for every row. A row is "pass" only when
> the observed behavior matches the acceptance criterion *and* the
> reviewer signs off in the Notes column. Any "fail" row blocks the
> Phase 3 → Phase 4 transition unless the row is explicitly waived in the
> waiver section at the bottom.

---

## Device matrix

| Device                | OS                | Network   | Build profile  | Install method     | Reviewer            | Date       | Build #  |
| --------------------- | ----------------- | --------- | -------------- | ------------------ | ------------------- | ---------- | -------- |
| iPhone 13 (Roma Nte.) | iOS 18.0          | LTE (Telcel) | development | TestFlight         |                     |            |          |
| iPhone 15 Pro         | iOS 18.1          | Wi-Fi        | preview     | TestFlight         |                     |            |          |
| Pixel 7               | Android 14        | LTE (AT&T MX) | development | Internal track APK |                     |            |          |
| Samsung A54           | Android 13        | LTE (Movistar MX) | preview | Internal track APK |                     |            |          |

Add rows as testers come online. Re-run for every release candidate.

---

## A. Install & first-run

| # | Step                                                        | Expected                                                                                  | iOS Result | Android Result | Notes |
| - | ----------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------- | -------------- | ----- |
| 1 | Install build from TestFlight / internal track              | App icon "Casa·Orquesta" with gold disc + navy "CO"                                       |            |                |       |
| 2 | First launch                                                | Splash with navy background → Onboarding screen renders ≤ 1.5 s                            |            |                |       |
| 3 | Read mic-permission rationale before OS dialog              | Bottom-sheet in es-MX explains why                                                        |            |                |       |
| 4 | Accept LFPDPPP consent gate                                 | Three checkboxes required, CTA disabled until all checked                                 |            |                |       |
| 5 | Decline a checkbox and try CTA                              | CTA stays at 0.45 opacity, no navigation                                                  |            |                |       |

## B. Voice happy path

| # | Step                                                                              | Expected                                                                              | iOS Result | Android Result | Notes |
| - | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ---------- | -------------- | ----- |
| 1 | Tap and hold mic, say *"busco departamento en Roma Norte"*                        | Halo turns green & pulses; transcript card shows partials live                        |            |                |       |
| 2 | Release mic                                                                       | Card flips to final transcript; halo → blue; status reads "Buscando…"                  |            |                |       |
| 3 | Assistant replies                                                                  | Audio plays through speaker within ≤ 1.5 s; locator-green chip activates              |            |                |       |
| 4 | Three `<ListingCard>` tiles drop in                                                | Cards have correct accent stripe (green), price in MXN format, match-score pill       |            |                |       |
| 5 | Tap a listing card                                                                 | Detail screen pushes; ★ "enfocado" appears next time you see the card                  |            |                |       |
| 6 | Say *"¿cuánto sería el predial?"* from Detail                                      | Audit-purple chip activates; AuditCard lands referring to the pinned listing          |            |                |       |
| 7 | Go back to Home                                                                    | Pinned listing card retains the gold ★ marker                                         |            |                |       |

**Latency target (Mexico LTE):** mic-release → first audible audio P50 ≤ 1.5 s, P95 ≤ 2.5 s. Time with a stopwatch; record the worst of 5 attempts. ☐ iOS:           ms / ☐ Android:           ms.

## C. Barge-in

| # | Step                                                                         | Expected                                                                       | iOS Result | Android Result | Notes |
| - | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ---------- | -------------- | ----- |
| 1 | Assistant is mid-reply (≥ 1 s of audio). User taps mic                       | Playback stops in ≤ 200 ms; halo turns green                                   |            |                |       |
| 2 | Assistant is mid-reply. User speaks loudly without tapping the mic           | Server-side BargeIn fires; cancel reaches client; playback stops in ≤ 400 ms   |            |                |       |
| 3 | Tap mic immediately after barge-in                                            | Recording restarts cleanly, no duplicate utterance                            |            |                |       |

## D. Reconnect

| # | Step                                                                    | Expected                                                                                  | iOS Result | Android Result | Notes |
| - | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------- | -------------- | ----- |
| 1 | Turn airplane mode on for 3 s in the middle of a turn                   | App shows "reconnecting…"; UI doesn't crash                                               |            |                |       |
| 2 | Turn airplane mode off                                                  | Socket resumes within ≤ 5 s using `/voice/.../{session_id}`; "resumed" event observed     |            |                |       |
| 3 | After resume, retry the last turn                                       | Pinned listing & focus still respected (server-side `VoiceSession.state` preserved)       |            |                |       |
| 4 | Kill the app fully, relaunch                                            | Onboarding skipped (state hydrated); fresh `session_id` issued on first PTT               |            |                |       |

## E. Mic level metering & UI

| # | Step                                                                  | Expected                                                                         | iOS Result | Android Result | Notes |
| - | --------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ---------- | -------------- | ----- |
| 1 | Speak softly                                                          | Halo pulse stays subtle; waveform bars hover near min                            |            |                |       |
| 2 | Speak loudly                                                          | Waveform bars expand smoothly (no twitching)                                     |            |                |       |
| 3 | Hold mic with no speech for 5 s                                       | No partials emitted; no false "thinking" state                                   |            |                |       |
| 4 | AgentChips reflect transitions                                        | Chip outline appears on `agent_start`; filled tint on `tool`; dims on `agent_end`|            |                |       |
| 5 | Settings → language toggle es-MX ↔ en-US                              | UI strings change immediately, no reload                                         |            |                |       |

## F. Performance & power

| Metric                                | Target                          | iOS Observed | Android Observed | Notes |
| ------------------------------------- | ------------------------------- | ------------ | ---------------- | ----- |
| Cold launch to Onboarding             | ≤ 2 s                            |              |                  |       |
| Cold launch to Home (returning user)  | ≤ 2.5 s                          |              |                  |       |
| First-audio-after-final P50 (LTE)     | ≤ 1.5 s                          |              |                  |       |
| First-audio-after-final P95 (LTE)     | ≤ 2.5 s                          |              |                  |       |
| Battery drop after 10 min voice use   | ≤ 6 %                            |              |                  |       |
| App size (downloaded)                 | ≤ 60 MB                          |              |                  |       |
| Memory peak during voice              | ≤ 220 MB                         |              |                  |       |
| Background → foreground recovery      | Audio resumes within 1 s         |              |                  |       |

## G. Edge cases

| # | Case                                                          | Expected                                                                  | iOS Result | Android Result | Notes |
| - | ------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------- | -------------- | ----- |
| 1 | Bluetooth headphones plugged in mid-session                   | Audio routes to headphones; no crash                                      |            |                |       |
| 2 | Phone call interrupts mid-session                             | Voice session pauses; resumes cleanly post-call                          |            |                |       |
| 3 | Spotify is playing when user taps mic                         | Spotify ducks/pauses; assistant audio plays through                       |            |                |       |
| 4 | Permission denied (mic) → user taps mic                       | Friendly "permiso necesario" sheet with "Abrir ajustes" affordance        |            |                |       |
| 5 | Slow network (throttle to 3G)                                 | UI shows "conectando…"; first audio degrades gracefully (no timeout < 8 s)|            |                |       |
| 6 | User backgrounds app mid-reply                                | No crash; audio continues if iOS allows; UI resumes cleanly on foreground |            |                |       |

## H. Accessibility

| # | Check                                                       | Expected                                                                  | iOS Result | Android Result | Notes |
| - | ----------------------------------------------------------- | ------------------------------------------------------------------------- | ---------- | -------------- | ----- |
| 1 | VoiceOver / TalkBack on mic button                          | Reads "Mantén presionado para hablar" (or current state label)            |            |                |       |
| 2 | VoiceOver / TalkBack on AgentChips                          | Reads "Auditor: tool" / "Buscador: active" etc.                          |            |                |       |
| 3 | Dynamic type at largest setting                              | No text truncated below readable; cards still tappable                    |            |                |       |
| 4 | Color contrast on all chips ≥ WCAG AA                       | Verified with Accessibility Inspector / Accessibility Scanner             |            |                |       |

## I. Compliance & legal

| # | Check                                                       | Expected                                                                  | iOS Result | Android Result | Notes |
| - | ----------------------------------------------------------- | ------------------------------------------------------------------------- | ---------- | -------------- | ----- |
| 1 | App Store privacy summary matches `store/metadata.json`     | All declared categories actually used at runtime                          |            |                |       |
| 2 | Play Data Safety form matches `store/metadata.json`         | Same as iOS, plus third-party share declarations (Deepgram, ElevenLabs)   |            |                |       |
| 3 | LFPDPPP "aviso de privacidad" link works                    | Opens https://casaorquesta.mx/aviso-de-privacidad in Safari/Chrome        |            |                |       |
| 4 | "Eliminar mi cuenta" path works                              | Settings → Eliminar → confirms → ARCO request flow opens                  |            |                |       |

## Waivers

If any row above is marked "fail" but the team agrees to ship anyway,
add a row here. Phase 4 sign-off must reference each open waiver.

| Row     | Reason                              | Mitigation                          | Reviewer    | Sign-off date | Resolved in |
| ------- | ----------------------------------- | ----------------------------------- | ----------- | ------------- | ----------- |
| _none_  |                                     |                                     |             |               |             |

## Sign-off

When the entire grid is filled and there are zero blocking failures,
two reviewers sign here. The signatures are also captured in the audit
log (Phase 4 wires the hash).

- iOS reviewer:                              date:
- Android reviewer:                          date:
- Phase 3 → Phase 4 gate:    open  ☐    closed  ☐
