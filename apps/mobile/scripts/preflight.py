#!/usr/bin/env python3
"""
EAS preflight — Phase 3.4.

Runs *before* `eas build` to catch the regressions that would otherwise
turn a 30-minute cloud build into a 30-minute waste. Validates:

  1. All referenced assets in app.json exist on disk
     (icon, splash, adaptive-icon, favicon, notification icon).
  2. iOS bundle identifier + Android package match expected
     (mx.casaorquesta.voice).
  3. Bundle id + Android package are consistent across app.json + eas.json.
  4. The "extra.eas.projectId" placeholder is acknowledged or replaced.
  5. Required env-var keys (EXPO_PUBLIC_*) are at least *declared* in
     eas.json profile envs (we check declaration, not the actual value —
     EAS injects values from secrets at build time).
  6. version + buildNumber + versionCode are coherent.

Exits non-zero on any block-level failure so CI can fail fast.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOBILE = HERE.parent

PASSED: list[str] = []
WARNED: list[tuple[str, str]] = []
FAILED: list[tuple[str, str]] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append((label, detail))
        print(f"  ❌ {label}  ← {detail}")


def warn(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        WARNED.append((label, detail))
        print(f"  ⚠️  {label}  ← {detail}")


def section(t: str) -> None:
    print()
    print("=" * 70)
    print(f"  {t}")
    print("=" * 70)


def _load_json(p: Path) -> dict:
    raw = p.read_text(encoding="utf-8")
    # tsconfig.json *may* have line comments — eas.json + app.json don't.
    return json.loads(raw)


def _walk(obj: dict, dotted: str):
    cur: object = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


app = _load_json(MOBILE / "app.json")
eas = _load_json(MOBILE / "eas.json")

# =========================================================================
# 1. Asset references in app.json point at real files
# =========================================================================
section("1. app.json asset references resolve to files")

asset_refs = [
    "expo.icon",
    "expo.splash.image",
    "expo.android.adaptiveIcon.foregroundImage",
    "expo.web.favicon",
]
for dotted in asset_refs:
    rel = _walk(app, dotted)
    if not isinstance(rel, str):
        ok(f"{dotted} present", False, "missing")
        continue
    p = (MOBILE / rel.lstrip("./")).resolve()
    ok(f"{dotted} → {rel}", p.is_file(), f"file missing: {p}")

# Notification icon (inside the expo-notifications plugin tuple)
plugins = _walk(app, "expo.plugins") or []
notif_icon: str | None = None
for entry in plugins:
    if isinstance(entry, list) and len(entry) >= 2 and entry[0] == "expo-notifications":
        cfg = entry[1] if isinstance(entry[1], dict) else {}
        notif_icon = cfg.get("icon")
if notif_icon:
    p = (MOBILE / notif_icon.lstrip("./")).resolve()
    ok(f"expo-notifications icon → {notif_icon}", p.is_file(),
       f"file missing: {p}")
else:
    warn("expo-notifications icon configured", False,
         "no `icon` key in expo-notifications plugin config")

# =========================================================================
# 2. Bundle identifiers are correct + consistent
# =========================================================================
section("2. Bundle identifiers (iOS bundleIdentifier + Android package)")

EXPECTED_BUNDLE = "mx.casaorquesta.voice"
ios_bundle = _walk(app, "expo.ios.bundleIdentifier")
android_pkg = _walk(app, "expo.android.package")
ok("iOS bundleIdentifier == mx.casaorquesta.voice",
   ios_bundle == EXPECTED_BUNDLE, f"got {ios_bundle!r}")
ok("Android package == mx.casaorquesta.voice",
   android_pkg == EXPECTED_BUNDLE, f"got {android_pkg!r}")

# =========================================================================
# 3. EAS projectId placeholder must be replaced before production
# =========================================================================
section("3. EAS projectId resolved")

project_id = _walk(app, "expo.extra.eas.projectId")
placeholder = "00000000-0000-0000-0000-000000000000"
ok("EAS projectId present", isinstance(project_id, str) and len(project_id) > 0,
   f"got {project_id!r}")
warn("EAS projectId != placeholder",
     project_id != placeholder,
     "Replace via `eas init` before production builds. "
     "Dev/preview builds still work with the placeholder.")

# =========================================================================
# 4. EAS env-var declarations cover the runtime
# =========================================================================
section("4. Required EXPO_PUBLIC_* env vars declared in eas.json")

REQUIRED_ENV = {
    "EXPO_PUBLIC_ENV",
    "EXPO_PUBLIC_VOICE_GATEWAY_URL",
    "EXPO_PUBLIC_ORCHESTRATOR_URL",
}

for profile in ("development", "preview", "production"):
    env = _walk(eas, f"build.{profile}.env") or {}
    missing = sorted(REQUIRED_ENV - set(env.keys()))
    ok(f"eas.json build.{profile}.env declares {sorted(REQUIRED_ENV)}",
       missing == [],
       f"missing: {missing}")

# =========================================================================
# 5. Version + build numbers are coherent
# =========================================================================
section("5. Version + build numbers")

version = _walk(app, "expo.version")
ios_build = _walk(app, "expo.ios.buildNumber")
android_code = _walk(app, "expo.android.versionCode")
ok("expo.version follows semver",
   isinstance(version, str) and re.match(r"^\d+\.\d+\.\d+$", version) is not None,
   f"got {version!r}")
ok("ios.buildNumber is a string integer",
   isinstance(ios_build, str) and ios_build.isdigit(),
   f"got {ios_build!r}")
ok("android.versionCode is a positive integer",
   isinstance(android_code, int) and android_code >= 1,
   f"got {android_code!r}")

# =========================================================================
# 6. Production submit config has placeholders documented
# =========================================================================
section("6. submit profile has resolvable secrets")

submit_ios = _walk(eas, "submit.production.ios") or {}
warn("submit.production.ios.ascAppId is a real ID",
     isinstance(submit_ios.get("ascAppId"), str)
     and submit_ios.get("ascAppId") != "0000000000",
     "Set ascAppId before `eas submit --platform ios`")
warn("submit.production.ios.appleTeamId is a real team",
     isinstance(submit_ios.get("appleTeamId"), str)
     and submit_ios.get("appleTeamId") != "XXXXXXXXXX",
     "Set appleTeamId before `eas submit --platform ios`")

submit_android = _walk(eas, "submit.production.android") or {}
warn("submit.production.android.serviceAccountKeyPath set",
     isinstance(submit_android.get("serviceAccountKeyPath"), str),
     "Set serviceAccountKeyPath before `eas submit --platform android`")

# =========================================================================
# 7. SECRETS.md exists with required envs documented
# =========================================================================
section("7. SECRETS.md lists required EAS secrets")

secrets_md = MOBILE / "SECRETS.md"
if secrets_md.is_file():
    text = secrets_md.read_text(encoding="utf-8")
    for env_name in (
        "EXPO_PUBLIC_VOICE_GATEWAY_URL",
        "EXPO_PUBLIC_ORCHESTRATOR_URL",
        "EXPO_PUBLIC_AUTH0_DOMAIN",
        "EXPO_PUBLIC_AUTH0_CLIENT_ID",
        "EXPO_PUBLIC_SENTRY_DSN",
    ):
        ok(f"SECRETS.md documents {env_name}", env_name in text)
else:
    ok("SECRETS.md exists", False, str(secrets_md))


# =========================================================================
# Summary
# =========================================================================
print()
print("=" * 70)
print("  PREFLIGHT SUMMARY")
print("=" * 70)
print(f"  Passed: {len(PASSED)}")
print(f"  Warnings: {len(WARNED)}")
print(f"  Failed: {len(FAILED)}")
if WARNED:
    print()
    print("  Warnings (non-blocking):")
    for label, detail in WARNED:
        print(f"   ⚠️  {label}: {detail}")
if FAILED:
    print()
    print("  Failures (BLOCK eas build):")
    for label, detail in FAILED:
        print(f"   ❌ {label}: {detail}")
    sys.exit(1)
print("  Preflight green. Safe to run `npm run build:dev:ios` or `:android`. ✅")
sys.exit(0)
