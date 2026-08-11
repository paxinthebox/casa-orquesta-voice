# Casa·Orquesta · Voice — Mobile Secrets & Env Vars

This file documents every secret the mobile app expects at build / runtime.
It is checked in. It contains **no actual secrets** — only the names, the
profile that needs them, and where to set them.

## How EAS injects values

EAS supports two mechanisms:

1. **Per-profile `env` block in `eas.json`.** Public values (URLs, env names,
   feature flags) live here. They are baked into the binary at build time
   and are visible to anyone who has the IPA / AAB. Use for non-secret
   configuration.
2. **EAS secrets.** Created with `eas secret:create --name FOO --value bar
   --scope project`. EAS injects them as env vars during the build.
   Use for any credential a user shouldn't be able to extract.

The `process.env.EXPO_PUBLIC_*` prefix is required for the value to be
exposed to the JS runtime. Anything without the prefix is build-time only.

## Required variables — by profile

| Variable                          | development               | preview                  | production               | Where to set                                  |
| --------------------------------- | ------------------------- | ------------------------ | ------------------------ | --------------------------------------------- |
| `EXPO_PUBLIC_ENV`                 | `development`             | `preview`                | `production`             | `eas.json` env (already done)                 |
| `EXPO_PUBLIC_VOICE_GATEWAY_URL`   | `wss://voice-dev…`        | `wss://voice-stage…`     | `wss://voice…`           | `eas.json` env                                |
| `EXPO_PUBLIC_ORCHESTRATOR_URL`    | `https://api-dev…`        | `https://api-stage…`     | `https://api…`           | `eas.json` env                                |
| `EXPO_PUBLIC_AUTH0_DOMAIN`        | `dev.auth0.com`           | `stage.auth0.com`        | `auth0.com`              | **EAS secret** (per scope+profile)            |
| `EXPO_PUBLIC_AUTH0_CLIENT_ID`     | per environment           | per environment          | per environment          | **EAS secret**                                |
| `EXPO_PUBLIC_SENTRY_DSN`          | per environment           | per environment          | per environment          | **EAS secret**                                |
| `SENTRY_AUTH_TOKEN`               | required at build time    | required at build time   | required at build time   | **EAS secret** (build-time only, no prefix)   |

For Sentry the `SENTRY_AUTH_TOKEN` is build-time only and used by
`@sentry/react-native` to upload dSYMs / mapping files. It is *not*
included in the JS bundle and does not start with `EXPO_PUBLIC_`.

## Creating EAS secrets — one-off setup

```bash
# Run from apps/mobile/
eas secret:create --name EXPO_PUBLIC_AUTH0_DOMAIN     --value <domain>     --scope project
eas secret:create --name EXPO_PUBLIC_AUTH0_CLIENT_ID  --value <client_id>  --scope project
eas secret:create --name EXPO_PUBLIC_SENTRY_DSN       --value <dsn>        --scope project
eas secret:create --name SENTRY_AUTH_TOKEN            --value <token>      --scope project --type string
```

For per-profile values use the `EAS_PROFILE` discriminator inside
`apps/mobile/scripts/select-secret.sh` (Phase 4 deliverable — for now we
use a single shared value per scope).

## Verifying secrets before a build

```bash
eas secret:list --scope project
./scripts/preflight.py    # validates eas.json env declarations
```

The preflight script does **not** validate that the EAS-managed secrets
actually exist (it has no access to them); it only checks that the
`eas.json` profile declares the public variables that the runtime needs.
If a secret is missing in EAS, the build will succeed but the runtime
will fail at the first call site — Auth0 will throw "domain undefined".

## Rotating Auth0

`react-native-auth0` reads `domain` + `clientId` from the JS bundle at
app launch. Rotating means a new EAS secret + a fresh build. There is
no over-the-air-updatable path for these values; that's intentional —
treating them as compile-time constants stops a stolen OTA bundle from
being able to silently redirect auth.

## Apple-side secrets (App Store Connect)

- `ASC_APP_ID` — App Store Connect numeric app id. Set in
  `eas.json > submit.production.ios.ascAppId`.
- `APPLE_TEAM_ID` — Apple Developer team id. Set in
  `eas.json > submit.production.ios.appleTeamId`.
- `APPLE_ID` — login email. Set in
  `eas.json > submit.production.ios.appleId` (Phase 4: move to EAS secret).

## Google-side secrets (Play Console)

- `play-service-account.json` — service-account JSON downloaded from
  Google Cloud Console. Must have the "Service Account User" role on
  the Play Console project. Path is configured at
  `eas.json > submit.production.android.serviceAccountKeyPath` and the
  file itself lives in `apps/mobile/secrets/` (gitignored, fetched at
  build time via `eas secret:download`).
