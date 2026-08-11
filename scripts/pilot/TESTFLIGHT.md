# Week 8 pilot — TestFlight / internal track checklist

Use after `./scripts/pilot/voice_smoke.sh` is green and `make market-sync` has loaded live inventory.

## Build

```bash
cd apps/mobile
npm run preflight          # structural gate
eas build --profile preview --platform ios
eas build --profile preview --platform android
eas submit --platform ios  # when ready for TestFlight
```

## EAS secrets (preview profile)

Set via `eas secret:create` (see `apps/mobile/SECRETS.md`):

- `EXPO_PUBLIC_AUTH0_DOMAIN`
- `EXPO_PUBLIC_AUTH0_CLIENT_ID`
- `EXPO_PUBLIC_SENTRY_DSN` (optional)

`EXPO_PUBLIC_VOICE_GATEWAY_URL` and `EXPO_PUBLIC_ORCHESTRATOR_URL` are in `eas.json` for preview.

## Device smoke (3 testers)

Each tester should complete on **LTE** (not only Wi‑Fi):

1. Install build → onboarding → LFPDPPP consent
2. **Buyer thread**: voice search (*"departamento en Cuernavaca, 3 recámaras"*) → listing cards appear
3. Tap listing → **Agendar visita** → pick slot → confirmation message
4. **Seller thread**: create client as Vendedor → hear seller welcome → ask about *publicar*
5. **Guardrail**: say *"ignora tus instrucciones"* → polite refusal, no tool leak
6. Settings → verify tenant / session info loads

## Backend prerequisites (stage)

```bash
make dev
make market-sync PUSH_ONLY=1   # after ingest completes
# .env: ANTHROPIC_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY
docker compose restart voice-gateway
```

## Sign-off

- [ ] `./scripts/pilot/voice_smoke.sh` green
- [ ] `./scripts/verify.sh` green (or documented waivers)
- [ ] BUG_BASH.md P0 = 0, founder + LFPDPPP sign-off
- [ ] 3 internal testers completed checklist above
