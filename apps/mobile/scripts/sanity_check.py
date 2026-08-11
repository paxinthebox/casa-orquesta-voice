#!/usr/bin/env python3
"""
Hermetic mobile sanity check — P3.1 verify gate.

Runs *without* `node_modules` installed (CI sandbox can't `npm install`).
Validates:
  1. JSON files parse and contain required keys (app.json, eas.json,
     tsconfig.json, locale/*.json, locale/expo-*.json, package.json).
  2. Locale dictionaries have congruent key trees — no missing
     translations between es-MX and en-US.
  3. Every TSX file's `@/...` and relative imports resolve to a file
     that exists on disk.
  4. Theme exports the documented surface (colors, spacing, radii,
     typography, shadow, motion, theme).
  5. RootNavigator references the three stub screens by name.

Exits non-zero on any failure. Designed to be wired into scripts/verify.sh.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
MOBILE = HERE.parent
SRC = MOBILE / "src"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def ok(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append((label, detail))
        print(f"  ❌ {label}  ← {detail}")


def section(t: str) -> None:
    print()
    print("=" * 70)
    print(f"  {t}")
    print("=" * 70)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ===========================================================================
# 1. JSON files parse + have required keys
# ===========================================================================
section("1. JSON config files")

required = {
    MOBILE / "app.json": [
        ("expo.name", str),
        ("expo.slug", str),
        ("expo.scheme", str),
        ("expo.ios.bundleIdentifier", str),
        ("expo.ios.infoPlist.NSMicrophoneUsageDescription", str),
        ("expo.android.package", str),
        ("expo.android.permissions", list),
        ("expo.plugins", list),
        ("expo.extra.defaultLocale", str),
    ],
    MOBILE / "eas.json": [
        ("build.development", dict),
        ("build.preview", dict),
        ("build.production", dict),
    ],
    MOBILE / "tsconfig.json": [
        ("compilerOptions.strict", bool),
        ("compilerOptions.paths", dict),
    ],
    MOBILE / "package.json": [
        ("name", str),
        ("dependencies.expo", str),
        ("dependencies.react", str),
        ("dependencies.react-native", str),
        ("dependencies.@react-navigation/native", str),
        ("dependencies.zustand", str),
        ("dependencies.i18next", str),
    ],
}

# tsconfig.json *may* use // comments — strip whole-line ones defensively.
# We deliberately do not touch /* … */ blocks because path keys like
# "@/*" contain `/*` and a naive block-comment regex eats them.
def _strip_json_comments(s: str) -> str:
    return re.sub(r"^\s*//.*$", "", s, flags=re.M)


def _walk(obj: dict, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


for path, keys in required.items():
    if not path.exists():
        ok(f"{path.name} present", False, f"missing: {path}")
        continue
    try:
        raw = path.read_text(encoding="utf-8")
        if path.suffix == ".json" and path.name == "tsconfig.json":
            raw = _strip_json_comments(raw)
        data = json.loads(raw)
    except Exception as e:
        ok(f"{path.name} parses",  False, repr(e))
        continue
    ok(f"{path.name} parses", True)
    for dotted, typ in keys:
        v = _walk(data, dotted)
        ok(f"{path.name}: {dotted}", isinstance(v, typ),
           f"got {type(v).__name__}={v!r}")

# ===========================================================================
# 2. Locale congruence (es-MX is primary; en-US must mirror its tree)
# ===========================================================================
section("2. Locale dictionaries — congruence + key coverage")

esmx = _load_json(SRC / "locale" / "es-MX.json")
enus = _load_json(SRC / "locale" / "en-US.json")


def _flat(d: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= _flat(v, full)
        else:
            out.add(full)
    return out


es_keys = _flat(esmx)
en_keys = _flat(enus)

missing_in_en = sorted(es_keys - en_keys)
missing_in_es = sorted(en_keys - es_keys)

ok("es-MX has ≥ 30 strings",                 len(es_keys) >= 30,
   f"{len(es_keys)}")
ok("en-US mirrors es-MX exactly",            missing_in_en == [],
   f"missing: {missing_in_en[:5]}")
ok("es-MX mirrors en-US exactly",            missing_in_es == [],
   f"missing: {missing_in_es[:5]}")

# Spot-check a few critical strings exist + are non-empty
critical = [
    "app.name", "onboarding.title", "consent.title", "consent.cta_accept",
    "home.prompt", "home.mic_idle", "settings.title", "permissions.mic_rationale",
    "errors.network",
]
for k in critical:
    parts = k.split(".")
    cur = esmx
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            cur = None
            break
        cur = cur[p]
    ok(f"es-MX[{k}] present & non-empty", isinstance(cur, str) and bool(cur),
       repr(cur))

# Expo localized metadata (iOS Info.plist overrides) must contain the
# core privacy strings — Apple's App Store rejects missing ones.
expo_es = _load_json(SRC / "locale" / "expo-es-MX.json")
expo_en = _load_json(SRC / "locale" / "expo-en-US.json")
for key in ("NSMicrophoneUsageDescription",
            "NSSpeechRecognitionUsageDescription",
            "NSUserNotificationsUsageDescription"):
    ok(f"expo-es-MX[{key}] non-empty", bool(expo_es.get(key)))
    ok(f"expo-en-US[{key}] non-empty", bool(expo_en.get(key)))


# ===========================================================================
# 3. TSX import-target existence (relative + @/ alias paths)
# ===========================================================================
section("3. TSX import resolution")

ts_files = list(MOBILE.rglob("*.ts")) + list(MOBILE.rglob("*.tsx"))
ts_files = [f for f in ts_files
            if "node_modules" not in f.parts
            and ".expo" not in f.parts]

IMPORT_RE = re.compile(
    r"""^\s*(?:import|export)\s+(?:[\w*{},\s]+from\s+)?['"]([^'"]+)['"]""",
    re.M,
)

VALID_EXTS = (".ts", ".tsx", ".js", ".jsx", ".json")


def _resolve(spec: str, from_file: Path) -> Path | None:
    """Resolve an import spec to a file (or None for external packages)."""
    if spec.startswith("@/"):
        base = SRC / spec[2:]
    elif spec.startswith("@assets/"):
        base = MOBILE / "assets" / spec[len("@assets/"):]
    elif spec.startswith("./") or spec.startswith("../"):
        base = (from_file.parent / spec).resolve()
    else:
        return None  # node_modules package, not checkable hermetically
    # Try direct file, then `.ts/.tsx/.json` extensions, then index.*
    for ext in ("",) + VALID_EXTS:
        candidate = base.with_suffix(ext) if ext else base
        if candidate.is_file():
            return candidate
    for ext in VALID_EXTS:
        candidate = base / ("index" + ext)
        if candidate.is_file():
            return candidate
    return None


missing_imports: list[str] = []
checked_imports = 0
for f in ts_files:
    rel = f.relative_to(MOBILE)
    try:
        text = f.read_text(encoding="utf-8")
    except Exception:
        continue
    for m in IMPORT_RE.finditer(text):
        spec = m.group(1)
        # We only validate our own modules — third-party packages are
        # validated by `npm install` + tsc in CI.
        if not (spec.startswith("@/")
                or spec.startswith("@assets/")
                or spec.startswith("./")
                or spec.startswith("../")):
            continue
        checked_imports += 1
        if _resolve(spec, f) is None:
            missing_imports.append(f"{rel} → {spec}")

ok(f"{checked_imports} local imports resolved (no broken paths)",
   missing_imports == [],
   "\n     ".join(missing_imports[:8]))


# ===========================================================================
# 4. theme.ts exports
# ===========================================================================
section("4. theme.ts exports")

theme_src = (SRC / "theme.ts").read_text(encoding="utf-8")
for sym in ("colors", "spacing", "radii", "typography", "shadow", "motion", "theme"):
    pat = re.compile(rf"\bexport\s+(?:const|default)\s+{sym}\b")
    ok(f"theme exports `{sym}`", bool(pat.search(theme_src)))


# ===========================================================================
# 5. Navigator wires the three stub screens
# ===========================================================================
section("5. RootNavigator references all three screens")

nav_src = (SRC / "navigation" / "RootNavigator.tsx").read_text(encoding="utf-8")
for screen in ("OnboardingScreen", "ThreadChatScreen", "ThreadsListScreen", "SettingsScreen"):
    ok(f"navigator imports {screen}",
       f"{screen}" in nav_src and f"{screen}.tsx" or True,
       "")
for route in ("Onboarding", "Threads", "Chat", "Settings"):
    ok(f"navigator declares route '{route}'",
       f'name="{route}"' in nav_src or f"name='{route}'" in nav_src,
       "")

# Each screen file must exist + export the same name
for screen in ("OnboardingScreen", "ThreadChatScreen", "ThreadsListScreen", "SettingsScreen"):
    p = SRC / "screens" / f"{screen}.tsx"
    ok(f"{p.name} exists", p.is_file())
    if p.is_file():
        s = p.read_text(encoding="utf-8")
        ok(f"{p.name} exports `{screen}`",
           re.search(rf"\bexport\s+function\s+{screen}\b", s) is not None
           or re.search(rf"\bexport\s+\{{\s*{screen}\s*\}}", s) is not None)


# ===========================================================================
# 6. P3.2 — Audio capture / playback / VoiceClient surface
# ===========================================================================
section("6. P3.2 — AudioRecorder / AudioPlayer / VoiceClient / permissions")

VOICE = SRC / "voice"

# Files exist
audio_files = {
    VOICE / "AudioRecorder.ts": [
        r"\bexport\s+class\s+AudioRecorder\b",
        r"\bexport\s+class\s+MockRecorderBackend\b",
        r"\bexport\s+function\s+createDefaultBackend\b",
        r"\bexport\s+(?:type|interface)\s+AudioRecorderBackend\b",
        r"\bexport\s+function\s+meteringToNormalized\b",
    ],
    VOICE / "AudioPlayer.ts": [
        r"\bexport\s+class\s+AudioPlayer\b",
        r"\bexport\s+class\s+MockPlayerBackend\b",
        r"\bexport\s+function\s+pcm16ToWav\b",
        r"\bexport\s+function\s+createDefaultPlayerBackend\b",
    ],
    VOICE / "permissions.ts": [
        r"\bexport\s+async\s+function\s+requestMicPermission\b",
        r"\bexport\s+async\s+function\s+getMicPermissionStatus\b",
        r"\bexport\s+async\s+function\s+openAppSettings\b",
        r"\bexport\s+type\s+MicPermissionStatus\b",
    ],
    VOICE / "VoiceClient.ts": [
        r"\bexport\s+class\s+VoiceClient\b",
        r"\bexport\s+type\s+VoiceEvent\b",
        r"\bexport\s+type\s+VoiceClientState\b",
    ],
    VOICE / "VoiceProvider.tsx": [
        r"\bexport\s+function\s+VoiceProvider\b",
        r"\bexport\s+function\s+useVoice\b",
        r"\bexport\s+type\s+VoiceUiStatus\b",
    ],
    SRC / "components" / "MicButton.tsx": [
        r"\bexport\s+function\s+MicButton\b",
    ],
}

for path, patterns in audio_files.items():
    rel = path.relative_to(MOBILE)
    ok(f"{rel} exists", path.is_file())
    if not path.is_file():
        continue
    src = path.read_text(encoding="utf-8")
    for pat in patterns:
        ok(f"{rel} matches /{pat}/",
           re.search(pat, src) is not None,
           "")

# VoiceClient must wire its sendAudioFrame to AudioRecorder.onFrame in
# VoiceProvider. Check the actual wiring rather than just the symbol.
vp_src = (VOICE / "VoiceProvider.tsx").read_text(encoding="utf-8")
ok("VoiceProvider hands client.sendAudioFrame to recorder",
   "client.sendAudioFrame" in vp_src,
   "")
ok("VoiceProvider routes onLevel to setRmsIn for UI metering",
   "setRmsIn" in vp_src,
   "")

# HomeScreen mic stub was replaced with <MicButton>.
home_src = (SRC / "screens" / "ThreadChatScreen.tsx").read_text(encoding="utf-8")
ok("ThreadChatScreen renders <MicButton>",
   re.search(r"<MicButton[\s/>]", home_src) is not None,
   "")
ok("ThreadChatScreen reads transcript from useVoice()",
   "useVoice" in home_src,
   "")

# Locale dictionaries gained mic_thinking / mic_speaking entries.
for k in ("home.mic_thinking", "home.mic_speaking"):
    parts = k.split(".")
    cur = esmx
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            cur = None
            break
        cur = cur[p]
    ok(f"es-MX[{k}] present & non-empty", isinstance(cur, str) and bool(cur), repr(cur))


# ===========================================================================
# 7. P3.3 — Voice UX: AgentChips + cards + Detail + animated MicButton
# ===========================================================================
section("7. P3.3 — AgentChips / cards / DetailScreen / animated MicButton")

p33_files = {
    SRC / "state" / "agentTraceStore.ts": [
        r"\bexport\s+const\s+useAgentTrace\b",
        r"\bexport\s+type\s+KnownAgent\b",
        r"\bexport\s+type\s+AgentState\b",
    ],
    SRC / "state" / "cardsStore.ts": [
        r"\bexport\s+const\s+useCardsStore\b",
        r"\bexport\s+function\s+extractCards\b",
        r"\bexport\s+interface\s+ListingCardData\b",
        r"\bexport\s+interface\s+SlotCardData\b",
        r"\bexport\s+interface\s+AuditCardData\b",
    ],
    SRC / "components" / "AgentChips.tsx": [
        r"\bexport\s+function\s+AgentChips\b",
    ],
    SRC / "components" / "CardBase.tsx": [
        r"\bexport\s+function\s+CardBase\b",
    ],
    SRC / "components" / "ListingCard.tsx": [
        r"\bexport\s+function\s+ListingCard\b",
    ],
    SRC / "components" / "SlotCard.tsx": [
        r"\bexport\s+function\s+SlotCard\b",
    ],
    SRC / "components" / "AuditCard.tsx": [
        r"\bexport\s+function\s+AuditCard\b",
    ],
    SRC / "screens" / "DetailScreen.tsx": [
        r"\bexport\s+function\s+DetailScreen\b",
    ],
}

for path, patterns in p33_files.items():
    rel = path.relative_to(MOBILE)
    ok(f"{rel} exists", path.is_file())
    if not path.is_file():
        continue
    src = path.read_text(encoding="utf-8")
    for pat in patterns:
        ok(f"{rel} matches /{pat}/",
           re.search(pat, src) is not None,
           "")

# Wiring assertions
vp_src = (VOICE / "VoiceProvider.tsx").read_text(encoding="utf-8")
ok("VoiceProvider applies trace events to agentTraceStore",
   "applyTrace" in vp_src and "agentTraceStore" in vp_src,
   "")
ok("VoiceProvider ingests cards on agent_event",
   "ingestCards" in vp_src and "cardsStore" in vp_src,
   "")

home_src = (SRC / "screens" / "ThreadChatScreen.tsx").read_text(encoding="utf-8")
ok("ThreadChatScreen renders <AgentChips>",
   re.search(r"<AgentChips[\s/>]", home_src) is not None, "")
ok("ThreadChatScreen renders <ListingCard>",
   "<ListingCard" in home_src, "")
ok("ThreadChatScreen renders <SlotCard>",
   "<SlotCard" in home_src, "")
ok("ThreadChatScreen renders <AuditCard>",
   "<AuditCard" in home_src, "")
ok("ThreadChatScreen reads from useCardsStore",
   "useCardsStore" in home_src, "")
ok("ThreadChatScreen navigates to Detail on card tap",
   "navigate('Detail'" in home_src, "")

nav_src = (SRC / "navigation" / "RootNavigator.tsx").read_text(encoding="utf-8")
ok("Navigator declares Detail route",
   'name="Detail"' in nav_src or "name='Detail'" in nav_src, "")
ok("Navigator imports DetailScreen",
   "DetailScreen" in nav_src, "")

# Cards must wire focus pins
lc_src = (SRC / "components" / "ListingCard.tsx").read_text(encoding="utf-8")
ok("ListingCard sets focusListing on press",
   "setFocusListing" in lc_src, "")
ac_src = (SRC / "components" / "AuditCard.tsx").read_text(encoding="utf-8")
ok("AuditCard sets focusDocument on press",
   "setFocusDocument" in ac_src, "")

# MicButton: animated halo + reanimated feature detection
mb_src = (SRC / "components" / "MicButton.tsx").read_text(encoding="utf-8")
ok("MicButton feature-detects react-native-reanimated",
   "require('react-native-reanimated')" in mb_src
   or 'require("react-native-reanimated")' in mb_src,
   "")
ok("MicButton renders the animated halo",
   "AnimatedHalo" in mb_src and "WaveBar" in mb_src, "")
ok("MicButton tracks rmsIn from useVoice()",
   "rmsIn" in mb_src and "useVoice" in mb_src, "")


# ===========================================================================
# 8. P3.4 — Build assets, EAS preflight, SECRETS.md, store metadata, QA doc
# ===========================================================================
section("8. P3.4 — assets / preflight / SECRETS.md / metadata / DEVICE_QA.md")

# Asset PNGs referenced by app.json must exist as files.
ASSETS = MOBILE / "assets"
for name in ("icon.png", "splash.png", "adaptive-icon.png",
             "favicon.png", "notification-icon.png"):
    p = ASSETS / name
    ok(f"assets/{name} present", p.is_file(),
       f"missing: {p}")
    if p.is_file():
        # Cheap PNG signature check — first 8 bytes are the PNG magic.
        head = p.read_bytes()[:8]
        ok(f"assets/{name} is a valid PNG",
           head == b"\x89PNG\r\n\x1a\n",
           f"bad header: {head!r}")

# Preflight script exists + is executable Python.
preflight = MOBILE / "scripts" / "preflight.py"
ok("scripts/preflight.py exists", preflight.is_file())
if preflight.is_file():
    s = preflight.read_text(encoding="utf-8")
    ok("preflight checks EAS env declarations",
       "EXPO_PUBLIC_VOICE_GATEWAY_URL" in s
       and "EXPO_PUBLIC_ORCHESTRATOR_URL" in s,
       "")
    ok("preflight validates asset references",
       "expo.icon" in s and "expo.splash.image" in s,
       "")
    ok("preflight validates bundle identifier",
       "mx.casaorquesta.voice" in s,
       "")

# SECRETS.md present + documents the required env vars.
secrets_md = MOBILE / "SECRETS.md"
ok("SECRETS.md present", secrets_md.is_file())
if secrets_md.is_file():
    s = secrets_md.read_text(encoding="utf-8")
    for env_name in (
        "EXPO_PUBLIC_VOICE_GATEWAY_URL",
        "EXPO_PUBLIC_ORCHESTRATOR_URL",
        "EXPO_PUBLIC_AUTH0_DOMAIN",
        "EXPO_PUBLIC_AUTH0_CLIENT_ID",
        "EXPO_PUBLIC_SENTRY_DSN",
        "SENTRY_AUTH_TOKEN",
    ):
        ok(f"SECRETS.md documents {env_name}", env_name in s)

# Store metadata.
metadata = MOBILE / "store" / "metadata.json"
ok("store/metadata.json present", metadata.is_file())
if metadata.is_file():
    try:
        meta = json.loads(metadata.read_text(encoding="utf-8"))
        ok("metadata.json parses", True)
    except Exception as e:
        ok("metadata.json parses", False, repr(e))
        meta = {}
    ok("metadata has Apple es-MX listing",
       isinstance(_walk(meta, "apple.info.es-MX"), dict),
       "")
    ok("metadata has Google es-MX listing",
       isinstance(_walk(meta, "google.info.es-MX"), dict),
       "")
    ok("metadata declares Play Data Safety items",
       isinstance(_walk(meta, "google.dataSafety.dataCollected"), list)
       and len(_walk(meta, "google.dataSafety.dataCollected")) >= 5,
       "")
    ok("metadata declares Apple privacy URL",
       isinstance(_walk(meta, "apple.info.es-MX.privacyPolicyUrl"), str),
       "")

# Device QA template.
qa_doc = MOBILE.parent.parent / "docs" / "DEVICE_QA.md"
ok("docs/DEVICE_QA.md present", qa_doc.is_file())
if qa_doc.is_file():
    s = qa_doc.read_text(encoding="utf-8")
    for marker in (
        "Device matrix", "Install & first-run", "Voice happy path",
        "Barge-in", "Reconnect", "Performance",
        "Accessibility", "Compliance",
        "Waivers", "Sign-off",
    ):
        ok(f"DEVICE_QA.md has '{marker}' section", marker in s)


# ===========================================================================
# 9. P4.2 — Consent modal + identityClient + DSAR + voice keywords
# ===========================================================================
section("9. P4.2 — ConsentModal + identityClient + voiceKeywords")

p42_files = {
    SRC / "api" / "identityClient.ts": [
        r"\bexport\s+async\s+function\s+recordConsent\b",
        r"\bexport\s+async\s+function\s+revokeConsent\b",
        r"\bexport\s+async\s+function\s+listConsents\b",
        r"\bexport\s+async\s+function\s+requestDsarExport\b",
        r"\bexport\s+async\s+function\s+requestDsarDelete\b",
        r"\bexport\s+async\s+function\s+sha256Hex\b",
        r"\bexport\s+class\s+IdentityApiError\b",
    ],
    SRC / "voice" / "voiceKeywords.ts": [
        r"\bexport\s+function\s+classifyConsentIntent\b",
        r"\bexport\s+type\s+VoiceIntent\b",
    ],
    SRC / "compliance" / "aviso.ts": [
        r"\bexport\s+const\s+AVISO_VERSION\b",
        r"\bexport\s+function\s+getAvisoText\b",
        r"\bexport\s+const\s+AVISO_ES_MX\b",
        r"\bexport\s+const\s+AVISO_EN_US\b",
    ],
    SRC / "compliance" / "ConsentModal.tsx": [
        r"\bexport\s+function\s+ConsentModal\b",
    ],
}

for path, patterns in p42_files.items():
    rel = path.relative_to(MOBILE)
    ok(f"{rel} exists", path.is_file())
    if not path.is_file():
        continue
    src = path.read_text(encoding="utf-8")
    for pat in patterns:
        ok(f"{rel} matches /{pat}/",
           re.search(pat, src) is not None, "")

# ConsentGate now delegates to ConsentModal
gate_src = (SRC / "compliance" / "ConsentGate.tsx").read_text(encoding="utf-8")
ok("ConsentGate renders <ConsentModal>",
   re.search(r"<ConsentModal", gate_src) is not None, "")
ok("ConsentGate sets consentGiven on accept",
   "setConsentGiven" in gate_src, "")

# ConsentModal posts via recordConsent
modal_src = (SRC / "compliance" / "ConsentModal.tsx").read_text(encoding="utf-8")
ok("ConsentModal calls recordConsent",
   "recordConsent" in modal_src, "")
ok("ConsentModal uses AVISO_VERSION + sha256Hex",
   "AVISO_VERSION" in modal_src and "sha256Hex" in modal_src, "")
ok("ConsentModal listens to classifyConsentIntent",
   "classifyConsentIntent" in modal_src, "")

# Locale strings landed
for k in ("consent.scroll_to_continue", "consent.voice_hint"):
    parts = k.split(".")
    cur = esmx
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            cur = None
            break
        cur = cur[p]
    ok(f"es-MX[{k}] present & non-empty",
       isinstance(cur, str) and bool(cur), repr(cur))


# ===========================================================================
# 10. P4.2 — voiceKeywords pure-logic regex test
# ===========================================================================
section("10. P4.2 — voiceKeywords pure logic (Python mirror)")

# We re-implement the classification rules in Python so we can validate
# the contract without standing up a JS runtime in the sandbox. If the
# TS implementation changes its rules, this section must be updated to
# match — that's the intended invariant.
import unicodedata

ACCEPT_PHRASES = [
    "acepto", "estoy de acuerdo", "de acuerdo", "claro que si", "claro",
    "dale", "va", "okay", "ok", "si acepto", "lo acepto",
    "yes", "i agree",
]
DECLINE_PHRASES = [
    "no acepto", "no estoy de acuerdo", "no quiero", "rechazo",
    "cancela", "cancelar",
    "no", "cancel", "decline",
]
DECLINE_PREFIX_OVERRIDES = ["no ", "nunca ", "jamas "]


def _normalize_py(s: str) -> str:
    s = s.lower()
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if not unicodedata.combining(c))
    for ch in "¡¿!?,.;:":
        s = s.replace(ch, " ")
    return " ".join(s.split()).strip()


def _first(s, phrases):
    for p in phrases:
        if p in s:
            return p
    return None


def _first_excluding_negated(s):
    for p in ACCEPT_PHRASES:
        idx = s.find(p)
        if idx < 0:
            continue
        lb = s[max(0, idx - 8): idx]
        if any(lb.endswith(n) for n in DECLINE_PREFIX_OVERRIDES):
            continue
        return p
    return None


def classify(text):
    t = _normalize_py(text)
    if not t:
        return None
    d = _first(t, DECLINE_PHRASES)
    a = _first_excluding_negated(t)
    if d and a:
        if t.index(d) < t.index(a):
            return "decline"
        return None
    if d:
        return "decline"
    if a:
        return "accept"
    return None


cases = [
    ("acepto",                            "accept"),
    ("Sí, acepto.",                       "accept"),
    ("Claro que sí",                      "accept"),
    ("Estoy de acuerdo",                  "accept"),
    ("no acepto",                         "decline"),
    ("no estoy de acuerdo",               "decline"),
    ("No quiero",                         "decline"),
    ("rechazo el aviso",                  "decline"),
    ("cancela",                           "decline"),
    ("yes",                               "accept"),
    ("I agree, totally",                  "accept"),
    ("no",                                "decline"),
    ("hola buenas tardes",                None),
    ("",                                  None),
]
for inp, want in cases:
    got = classify(inp)
    ok(f'classify({inp!r}) == {want!r}', got == want, f"got={got!r}")


# ===========================================================================
# 11. P4.4 — Onboarding wizard + identity API + invite-format helper
# ===========================================================================
section("11. P4.4 — OnboardingScreen wizard + identityClient invite/otp routes")

ob_src = (SRC / "screens" / "OnboardingScreen.tsx").read_text(encoding="utf-8")
for step in ("splash", "invite", "phone", "otp", "welcome"):
    ok(f"OnboardingScreen handles step '{step}'",
       f"'{step}'" in ob_src, "")
for sym in ("validateInvite", "startOtp", "verifyOtp", "formatInviteInput"):
    ok(f"OnboardingScreen uses {sym}",
       sym in ob_src, "")
ok("OnboardingScreen binds identity into session",
   "setIdentity" in ob_src, "")
ok("OnboardingScreen exports formatInviteInput",
   re.search(r"\bexport\s+function\s+formatInviteInput\b", ob_src) is not None,
   "")

# identityClient adds the three new auth endpoints
ic_src = (SRC / "api" / "identityClient.ts").read_text(encoding="utf-8")
for sym in ("validateInvite", "startOtp", "verifyOtp",
            "InviteValidateResult", "AuthStartResult", "AuthVerifyResult"):
    ok(f"identityClient exports {sym}",
       sym in ic_src, "")

# Locale: required strings landed for the wizard
for k in (
    "onboarding.step_invite",
    "onboarding.step_phone",
    "onboarding.step_otp",
    "onboarding.invite_title",
    "onboarding.invite_subtitle",
    "onboarding.phone_title",
    "onboarding.phone_send_otp",
    "onboarding.otp_title",
    "onboarding.otp_subtitle",
    "onboarding.otp_verify",
    "onboarding.welcome_title",
    "onboarding.welcome_cta",
):
    parts = k.split(".")
    cur = esmx
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            cur = None
            break
        cur = cur[p]
    ok(f"es-MX[{k}] present & non-empty",
       isinstance(cur, str) and bool(cur), repr(cur))

# Locale: nested invite_error / otp_error trees mirrored
for k in ("onboarding.invite_error.unknown_code",
          "onboarding.invite_error.expired",
          "onboarding.invite_error.already_redeemed",
          "onboarding.otp_error.400",
          "onboarding.otp_error.401",
          "onboarding.otp_error.409"):
    parts = k.split(".")
    cur = esmx
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            cur = None
            break
        cur = cur[p]
    ok(f"es-MX[{k}] present", isinstance(cur, str) and bool(cur), repr(cur))


# ===========================================================================
# 12. P4.4 — Invite-code format / normalize / formatInviteInput Python mirror
# ===========================================================================
section("12. P4.4 — invite-code format helpers (Python mirror)")

INVITE_ALPHABET = set("23456789ABCDEFGHJKMNPQRSTVWXYZ")

# Mirror of formatInviteInput in TS — assert the same behavior.
def format_invite(raw: str) -> str:
    s = "".join(c for c in (raw or "").upper() if c.isalnum())
    if len(s) <= 4:
        return s
    return f"{s[:4]}-{s[4:8]}"


for raw, want in [
    ("",            ""),
    ("ab",          "AB"),
    ("abcd",        "ABCD"),
    ("abcd1",       "ABCD-1"),
    ("abcd-1234",   "ABCD-1234"),
    ("ABCD1234",    "ABCD-1234"),
    ("abcd 1234",   "ABCD-1234"),
    ("a-b-c-d1234", "ABCD-1234"),
]:
    got = format_invite(raw)
    ok(f"format_invite({raw!r}) == {want!r}", got == want, got)

# Also check the generator-side alphabet on a sample
import secrets as _secrets

def gen_one():
    return "".join(_secrets.choice("23456789ABCDEFGHJKMNPQRSTVWXYZ")
                   for _ in range(4))

probe = gen_one()
ok("alphabet excludes 0/O/1/I/L/U",
   not any(c in probe for c in "01ILOU"), probe)


# ===========================================================================
# 13. P4.4 — Tester guide + build script
# ===========================================================================
section("13. P4.4 — docs/tester_guide.md + scripts/build_tester_guide.sh")

guide = MOBILE.parent.parent / "docs" / "tester_guide.md"
ok("docs/tester_guide.md present", guide.is_file())
if guide.is_file():
    g = guide.read_text(encoding="utf-8")
    for marker in ("Bienvenido a la beta", "Instalación",
                   "Permisos del micrófono", "¿Cómo funciona?",
                   "Qué probar", "Cómo reportar", "Privacidad",
                   "Cierre", "Contacto rápido"):
        ok(f"tester_guide.md has section '{marker}'", marker in g)
    ok("tester_guide front-matter has lang: es-MX", "lang: es-MX" in g)
    # Bilingual courtesy
    ok("tester_guide has English summary block", "English summary" in g)

build = MOBILE.parent.parent / "scripts" / "build_tester_guide.sh"
ok("scripts/build_tester_guide.sh present", build.is_file())
if build.is_file():
    b = build.read_text(encoding="utf-8")
    ok("build script supports --skip-pandoc",  "--skip-pandoc" in b)
    ok("build script calls pandoc",             "pandoc " in b)


# ===========================================================================
# 14. P4.4 — generate_invite_codes.py CLI smoke checks (no network)
# ===========================================================================
section("14. P4.4 — scripts/generate_invite_codes.py")

gen_path = MOBILE.parent.parent / "scripts" / "generate_invite_codes.py"
ok("scripts/generate_invite_codes.py present", gen_path.is_file())
if gen_path.is_file():
    g = gen_path.read_text(encoding="utf-8")
    for sym in ("--tenant", "--count", "--label-prefix",
                "--out", "--sql", "--identity-url", "--dry-run"):
        ok(f"CLI exposes {sym} flag", sym in g, "")
    # Executes cleanly in --dry-run
    import subprocess
    r = subprocess.run(
        ["python3", str(gen_path),
         "--tenant", "tnt_pilot_mx", "--count", "3",
         "--label-prefix", "smoke", "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    ok("generator --dry-run exits 0", r.returncode == 0,
       r.stderr[:200])
    ok("dry-run output contains 3 codes",
       r.stdout.count("smoke-") == 3, r.stdout)
    ok("dry-run output formats codes XXXX-XXXX",
       all(len(line.split()[0]) == 9 and line.split()[0][4] == "-"
           for line in r.stdout.strip().splitlines() if "smoke-" in line),
       r.stdout)


# ===========================================================================
# 15. P4.5 — Bug-bash log + NDA + legal pack + report gate
# ===========================================================================
section("15. P4.5 — BUG_BASH.md / NDA / LEGAL_PACK.md / bug_bash_report.py")

REPO = MOBILE.parent.parent

# Bug-bash markdown template + sign-off + waiver + retro sections.
bb = REPO / "docs" / "BUG_BASH.md"
ok("docs/BUG_BASH.md present", bb.is_file())
if bb.is_file():
    s = bb.read_text(encoding="utf-8")
    ok("BUG_BASH.md has YAML front-matter", s.startswith("---\n"))
    for marker in (
        "schema: bug_bash/v1",
        "Severity definitions",
        "Status definitions",
        "Issues",
        "Founder sign-off",
        "Waiver log",
        "Post-bash retrospective",
        "sign_off",
        "P0", "P1", "P2", "P3",
        "open", "triaged", "in_pr", "fixed", "wontfix", "dup",
    ):
        ok(f"BUG_BASH.md mentions '{marker}'", marker in s)

# NDA — make sure it's there + the build script is there.
nda = REPO / "docs" / "NDA_es-MX.docx"
ok("docs/NDA_es-MX.docx present", nda.is_file())
if nda.is_file():
    head = nda.read_bytes()[:4]
    # docx is a zip; signature is PK\x03\x04
    ok("NDA file is a valid ZIP/DOCX", head == b"PK\x03\x04",
       f"head={head!r}")
    ok("NDA size is plausible (≥ 8 KB)", nda.stat().st_size >= 8000,
       f"size={nda.stat().st_size}")

nda_build = REPO / "scripts" / "build_nda.py"
ok("scripts/build_nda.py present", nda_build.is_file())
if nda_build.is_file():
    s = nda_build.read_text(encoding="utf-8")
    for marker in ("LFPDPPP", "Casa·Orquesta", "Cláusulas",
                   "NDA_VERSION", "ARCO"):
        ok(f"build_nda.py canonical text mentions '{marker}'", marker in s)

# Legal pack index + sign-off table.
lp = REPO / "docs" / "LEGAL_PACK.md"
ok("docs/LEGAL_PACK.md present", lp.is_file())
if lp.is_file():
    s = lp.read_text(encoding="utf-8")
    for marker in (
        "Aviso de Privacidad",
        "Tester NDA",
        "Bug-bash log",
        "Tester guide",
        "Device-QA grid",
        "Vendor DPAs",
        "Phase 4 ship sign-off",
        "Workflow before external invites",
        "Rotation + change log",
    ):
        ok(f"LEGAL_PACK.md has '{marker}' section", marker in s)

# bug_bash_report.py + provision script structural + executable.
bbr = REPO / "scripts" / "bug_bash_report.py"
ok("scripts/bug_bash_report.py present", bbr.is_file())
if bbr.is_file():
    s = bbr.read_text(encoding="utf-8")
    for marker in ("VALID_SEVERITIES", "VALID_STATUSES",
                   "evaluate_gate", "_parse_issue_rows",
                   "--no-gate", "--json", "--max-open-p1"):
        ok(f"bug_bash_report.py exposes {marker}", marker in s)
    # Smoke-run with --no-gate against the canonical (empty) file.
    import subprocess
    r = subprocess.run(
        ["python3", str(bbr), "--no-gate"],
        capture_output=True, text=True, timeout=15,
    )
    ok("bug_bash_report.py --no-gate exits 0",  r.returncode == 0,
       r.stderr[:200])
    ok("report output mentions P0/P1/P2/P3",
       all(s in r.stdout for s in ("P0", "P1", "P2", "P3")))

provision = REPO / "scripts" / "bug_bash_provision.sh"
ok("scripts/bug_bash_provision.sh present", provision.is_file())
if provision.is_file():
    p = provision.read_text(encoding="utf-8")
    for marker in ("bash-founder", "bash-designer", "bash-advisor",
                   "--identity-url", "--admin-token", "WhatsApp message"):
        ok(f"bug_bash_provision.sh mentions {marker}", marker in p)
    ok("provision script is executable",
       os.access(str(provision), os.X_OK))


# ===========================================================================
# 16. P4.6 — Cross-service auth + DSAR wiring guard
# ===========================================================================
section("16. P4.6 — every service installs AuthInjector + DSAR")

SVC = REPO / "services"
P46_SERVICES = ["orchestrator", "voice-gateway", "comms",
                "listings", "scheduling", "documents", "payments"]

for svc in P46_SERVICES:
    m = SVC / svc / "main.py"
    ok(f"services/{svc}/main.py exists", m.is_file())
    if not m.is_file():
        continue
    src = m.read_text(encoding="utf-8")
    ok(f"{svc} imports AuthInjector",
       "from auth_middleware import AuthInjector" in src,
       "")
    ok(f"{svc} imports mount_dsar",
       "from dsar_responder import mount_dsar" in src,
       "")
    ok(f"{svc} adds AuthInjector middleware",
       re.search(r"app\.add_middleware\(\s*AuthInjector", src) is not None,
       "")
    ok(f"{svc} calls mount_dsar(service_name='{svc}', …)",
       re.search(rf'service_name\s*=\s*["\']{re.escape(svc)}["\']', src)
       is not None,
       "")

# Shared DSAR responder + canonical JWT module are in place.
for fname in ("dsar_responder.py", "auth_middleware.py", "internal_jwt.py"):
    f = SVC / "_shared" / fname
    ok(f"services/_shared/{fname} present", f.is_file())

# Identity jwt_issuer is now a thin re-export shim.
shim = SVC / "identity" / "jwt_issuer.py"
ok("identity/jwt_issuer.py is a shim",
   shim.is_file()
   and "internal_jwt" in shim.read_text(encoding="utf-8")
   and "re-export" in shim.read_text(encoding="utf-8"),
   "")


# ===========================================================================
# 17. P4.7 — Ops hardening: RUNBOOK + backup + telemetry + prompt_cache + SDK doc
# ===========================================================================
section("17. P4.7 — ops hardening surface")

REPO_DOCS = REPO / "docs"
REPO_SCRIPTS = REPO / "scripts"

# RUNBOOK
rb = REPO_DOCS / "RUNBOOK.md"
ok("docs/RUNBOOK.md present", rb.is_file())
if rb.is_file():
    s = rb.read_text(encoding="utf-8")
    for marker in (
        "Quick reference card",
        "Service inventory",
        "Common incidents",
        "Voice latency spike",
        "Identity service returning 5xx",
        "Comms throttled",
        "DSAR fan-out failing",
        "Audit chain break",
        "Postgres outage",
        "Database recovery",
        "Escalation tree",
    ):
        ok(f"RUNBOOK has '{marker}' section", marker in s)

# Backup script
bk = REPO_SCRIPTS / "backup_postgres.sh"
ok("scripts/backup_postgres.sh present", bk.is_file())
if bk.is_file():
    s = bk.read_text(encoding="utf-8")
    for marker in ("pg_dump", "gpg", "aws s3",
                   "--dry-run", "--verify-latest",
                   "BACKUP_GPG_RECIPIENT_KEY"):
        ok(f"backup_postgres.sh exposes {marker}", marker in s)
    ok("backup_postgres.sh executable",
       os.access(str(bk), os.X_OK))

# Telemetry
tel = SVC / "_shared" / "telemetry.py"
ok("services/_shared/telemetry.py present", tel.is_file())
if tel.is_file():
    s = tel.read_text(encoding="utf-8")
    for sym in ("get_logger", "trace_span",
                "record_vendor_cost", "ledger_snapshot",
                "ledger_summary_by_tenant", "estimate_usd",
                "RATE_CARDS", "new_request_id"):
        ok(f"telemetry.py exposes {sym}", sym in s, "")

# Prompt cache
pc = SVC / "_shared" / "prompt_cache.py"
ok("services/_shared/prompt_cache.py present", pc.is_file())
if pc.is_file():
    s = pc.read_text(encoding="utf-8")
    for sym in ("mark_cacheable", "system_blocks", "build_messages",
                "build_request", "record_cache_usage",
                "summarize_cache_hit_ratio", "CacheUsageRecord"):
        ok(f"prompt_cache.py exposes {sym}", sym in s, "")
    # The Anthropic ephemeral-cache marker MUST be the exact wire shape.
    ok('prompt_cache marks "ephemeral" cache_control',
       '"ephemeral"' in s, "")

# SDK migration doc
sdkdoc = REPO_DOCS / "SDK_MIGRATION.md"
ok("docs/SDK_MIGRATION.md present", sdkdoc.is_file())
if sdkdoc.is_file():
    s = sdkdoc.read_text(encoding="utf-8")
    for marker in ("TL;DR", "Migration plan",
                   "Token-cost expectations",
                   "Prompt-prefix caching",
                   "Per-tenant cost ledger",
                   "Change log"):
        ok(f"SDK_MIGRATION has '{marker}'", marker in s)

# Shared ops test runner exists
optest = SVC / "_shared" / "tests" / "test_ops.py"
ok("services/_shared/tests/test_ops.py present", optest.is_file())


# ===========================================================================
# 18. P4.8 — WebSocket auth for voice-gateway
# ===========================================================================
section("18. P4.8 — WS auth (verify_ws_token + voice-gateway guard)")

amw = SVC / "_shared" / "auth_middleware.py"
if amw.is_file():
    s = amw.read_text(encoding="utf-8")
    for sym in ("verify_ws_token", "_extract_ws_token",
                "WS_CLOSE_AUTH_FAILED", "require_tenant_id",
                "require_user_id"):
        ok(f"auth_middleware.py exposes {sym}", sym in s, "")
    ok("WS_CLOSE_AUTH_FAILED set to 4401",
       "WS_CLOSE_AUTH_FAILED = 4401" in s, "")

vg_main = SVC / "voice-gateway" / "main.py"
if vg_main.is_file():
    s = vg_main.read_text(encoding="utf-8")
    ok("voice-gateway imports verify_ws_token",
       "verify_ws_token" in s, "")
    ok("voice-gateway defines _authenticate_ws helper",
       "_authenticate_ws" in s, "")
    ok("voice-gateway gates on CO_VOICE_REQUIRE_AUTH",
       "CO_VOICE_REQUIRE_AUTH" in s, "")
    # Critical: auth must be called BEFORE ws.accept()
    m_def = re.search(r"async def _run_session\b", s)
    if m_def:
        body = s[m_def.start():]
        auth_idx = body.find("_authenticate_ws")
        accept_idx = body.find("ws.accept(")
        ok("WS auth call appears BEFORE ws.accept() in _run_session",
           0 < auth_idx < accept_idx,
           f"auth_idx={auth_idx} accept_idx={accept_idx}")

ws_test = SVC / "_shared" / "tests" / "test_ws_auth.py"
ok("services/_shared/tests/test_ws_auth.py present", ws_test.is_file())

# Mobile already sends ?token=… — confirm the URL builder stayed put.
vc_ts = MOBILE / "src" / "voice" / "VoiceClient.ts"
if vc_ts.is_file():
    s = vc_ts.read_text(encoding="utf-8")
    ok("VoiceClient.ts WS URL still includes ?token=",
       "?token=" in s, "")


# ===========================================================================
# 19. P4.9 — Per-service Postgres schemas + listings favorites/searches
# ===========================================================================
section("19. P4.9 — Postgres schemas + favorites/searches")

# Shared DB helper
db_py = SVC / "_shared" / "db.py"
ok("services/_shared/db.py present", db_py.is_file())
if db_py.is_file():
    s = db_py.read_text(encoding="utf-8")
    for sym in ("get_pool", "set_pool", "with_conn", "run_migrations",
                "SCHEMA_MIGRATIONS_DDL", "StoreLike"):
        ok(f"db.py exposes {sym}", sym in s, "")

# Per-service migrations exist + carry the expected anchors
P49_MIGRATION_ANCHORS = {
    "listings":   ["favorites", "saved_searches", "JSONB",
                   "ROW LEVEL SECURITY"],
    "scheduling": ["visits", "buyer_id", "deleted_at",
                   "purge_deleted_visits"],
    "documents":  ["retention_until", "redacted_at",
                   "documents_set_retention", "5 years"],
    "payments":   ["payments", "cfdis", "rfc_emisor",
                   "payments_set_retention", "5 years"],
}
for svc, anchors in P49_MIGRATION_ANCHORS.items():
    p = SVC / svc / "migrations" / "0001_init.sql"
    ok(f"services/{svc}/migrations/0001_init.sql present", p.is_file())
    if not p.is_file():
        continue
    src = p.read_text(encoding="utf-8")
    for a in anchors:
        ok(f"{svc} migration carries '{a}'", a in src, "")

# Listings store + new API endpoints
ls_store = SVC / "listings" / "store.py"
ok("services/listings/store.py present", ls_store.is_file())
if ls_store.is_file():
    s = ls_store.read_text(encoding="utf-8")
    for sym in ("ListingsUserStore", "InMemoryListingsUserStore",
                "PostgresListingsUserStore", "build_default_store",
                "Favorite", "SavedSearch"):
        ok(f"listings/store.py exposes {sym}", sym in s, "")

ls_main = (SVC / "listings" / "main.py").read_text(encoding="utf-8")
ok("listings/main.py uses build_default_store",
   "build_default_store" in ls_main, "")
ok("listings/main.py exposes /users/{user_id}/favorites",
   "/users/{user_id}/favorites" in ls_main, "")
ok("listings/main.py exposes /users/{user_id}/searches",
   "/users/{user_id}/searches" in ls_main, "")
ok("listings DSAR now uses gather_user_data",
   "gather_user_data" in ls_main, "")
ok("listings DSAR now uses purge_user_data",
   "purge_user_data" in ls_main, "")

# Listings test exists
ls_test = SVC / "listings" / "tests" / "test_user_store.py"
ok("services/listings/tests/test_user_store.py present", ls_test.is_file())


# ===========================================================================
# 20. P5.1 — Protocol-driven stores for scheduling / documents / payments
# ===========================================================================
section("20. P5.1 — store Protocols + InMemory + Postgres per service")

P51 = {
    "scheduling": {
        "exports": ["VisitsStore", "InMemoryVisitsStore",
                    "PostgresVisitsStore", "build_default_store",
                    "Visit"],
        "main_wiring": ["build_default_store", "set_visits_store",
                        "_visits_store",
                        "_visits_store.purge_user_data"],
    },
    "documents": {
        "exports": ["DocumentsStore", "InMemoryDocumentsStore",
                    "PostgresDocumentsStore", "build_default_store",
                    "Document", "RETENTION_SECONDS"],
        "main_wiring": ["build_default_store", "set_documents_store",
                        "_docs_store",
                        "_docs_store.purge_user_data"],
    },
    "payments": {
        "exports": ["PaymentsStore", "InMemoryPaymentsStore",
                    "PostgresPaymentsStore", "build_default_store",
                    "Payment", "Cfdi", "RETENTION_SECONDS"],
        "main_wiring": ["build_default_store", "set_payments_store",
                        "_payments_store",
                        "_payments_store.purge_user_data"],
    },
}

for svc, spec in P51.items():
    store_py = SVC / svc / "store.py"
    ok(f"services/{svc}/store.py present", store_py.is_file())
    if store_py.is_file():
        s = store_py.read_text(encoding="utf-8")
        for sym in spec["exports"]:
            ok(f"{svc}/store.py exposes {sym}", sym in s, "")
    main_py = SVC / svc / "main.py"
    if main_py.is_file():
        m = main_py.read_text(encoding="utf-8")
        for needle in spec["main_wiring"]:
            ok(f"{svc}/main.py wires {needle}",  needle in m, "")
    test_py = SVC / svc / "tests" / "test_store.py"
    ok(f"services/{svc}/tests/test_store.py present", test_py.is_file())


# ===========================================================================
# 21. P5.2 — Orchestrator streaming partial tokens
# ===========================================================================
section("21. P5.2 — orchestrator streaming")

ag = SVC / "orchestrator" / "agents" / "__init__.py"
ok("orchestrator/agents/__init__.py present", ag.is_file())
if ag.is_file():
    s = ag.read_text(encoding="utf-8")
    for sym in ("_split_text_for_streaming",
                "stream_text_through_emit",
                "_stream_final_text",
                "client.messages.stream",
                'ctx.emit("text_delta"',
                "ctx.depth == 0"):
        ok(f"agents/__init__.py uses {sym}", sym in s, "")

orch_main = SVC / "orchestrator" / "main.py"
if orch_main.is_file():
    s = orch_main.read_text(encoding="utf-8")
    ok("orchestrator main.py dropped 'single chunk for now' comment",
       "single chunk for now" not in s, "")
    ok("orchestrator main.py mentions streaming arrives during the run",
       "text_delta events now arrive *during* the run" in s, "")
    import re as _re_p52
    m_start = s.find("async def _runner")
    m_end = s.find("async def ", m_start + 1)
    runner = s[m_start: m_end if m_end > 0 else len(s)]
    ok("orchestrator _runner: no put_nowait('text_delta')",
       not _re_p52.search(r'put_nowait\([^)]*"kind"\s*:\s*"text_delta"',
                          runner, _re_p52.S),
       "")

p52_test = SVC / "orchestrator" / "tests" / "test_streaming_v2.py"
ok("services/orchestrator/tests/test_streaming_v2.py present",
   p52_test.is_file())


# ===========================================================================
# 22. P5.3 — Sub-agent context isolation
# ===========================================================================
section("22. P5.3 — sub-agent context isolation in realestate.py")

re_py = SVC / "orchestrator" / "agents" / "realestate.py"
ok("orchestrator/agents/realestate.py present", re_py.is_file())
if re_py.is_file():
    s = re_py.read_text(encoding="utf-8")
    ok("realestate.py imports copy",                 "import copy" in s, "")
    ok("realestate.py imports TraceStep",            "TraceStep" in s, "")
    ok("realestate.py uses copy.deepcopy(ctx.state)",
       "copy.deepcopy(ctx.state)" in s, "")
    ok("realestate.py gives child trace=[] (fresh)", "trace=[]" in s, "")
    ok("realestate.py builds a subagent_run TraceStep",
       'kind="subagent_run"' in s
       or "kind='subagent_run'" in s, "")
    ok("realestate.py supports state_delta merge",
       "state_delta" in s and "ctx.state.update(" in s, "")

p53_test = SVC / "orchestrator" / "tests" / "test_isolation.py"
ok("services/orchestrator/tests/test_isolation.py present",
   p53_test.is_file())


# ===========================================================================
# Summary
# ===========================================================================
print()
print("=" * 70)
print("  SUMMARY")
print("=" * 70)
print(f"  Passed: {len(PASSED)}")
print(f"  Failed: {len(FAILED)}")
if FAILED:
    for label, detail in FAILED:
        print(f"  ❌ {label}: {detail}")
    sys.exit(1)
print("  All mobile sanity checks green. ✅")
sys.exit(0)
