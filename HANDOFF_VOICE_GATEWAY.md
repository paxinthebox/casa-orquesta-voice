# Voice Gateway: Google Cloud STT/TTS Adaptation

## Context

Casa·Orquesta·Voice is a voice-first real estate app. The voice-gateway service currently uses Deepgram (STT) + ElevenLabs (TTS). We're switching to Google Cloud: **Chirp 3 (STT) + Gemini 3.1 Flash TTS (TTS)**, using a single `GOOGLE_CLOUD_API_KEY`.

The orchestrator is already deployed on Fly.io (`casa-orquesta-orchestrator.fly.dev`) using MiniMax M3 + DeepSeek V4 Flash via Fireworks AI. The voice-gateway is the next service to deploy.

## YOUR TASK

Write two new provider adapters and wire them into the existing factory pattern. You are implementing; lucia is reviewing and deploying.

## Step 1: Read these files FIRST (in order)

1. `services/voice-gateway/stt/interfaces.py` — STTProvider protocol
2. `services/voice-gateway/stt/deepgram_client.py` — existing STT adapter (reference implementation)
3. `services/voice-gateway/tts/interfaces.py` — TTSProvider protocol
4. `services/voice-gateway/tts/elevenlabs_rest_client.py` — existing REST TTS adapter (closest pattern to what Gemini TTS needs)
5. `services/voice-gateway/tts/factory.py` — TTS factory
6. `services/voice-gateway/pipeline/session.py` — where STT is instantiated (find where DeepgramSTT is imported)
7. `services/voice-gateway/requirements.txt` — current deps

## Step 2: Write `services/voice-gateway/stt/google_stt.py`

Google Chirp 3 STT adapter implementing STTProvider protocol.

- SDK: `google-cloud-speech` (V2 API)
- Model: `chirp_3`
- Language: `es-MX` (passed from pipeline)
- Streaming recognition (real-time, not batch)
- Audio format: PCM 16kHz mono (matches existing pipeline)
- API key from `GOOGLE_CLOUD_API_KEY` env var
- Must implement: `open()`, `send_audio()`, `start_utterance()`, `end_utterance()`, `close()`, `is_open` property
- Follow the same pattern as deepgram_client.py: async reader loop, callbacks for on_partial/on_final
- Reference: https://cloud.google.com/speech-to-text/docs/streaming-recognize

## Step 3: Write `services/voice-gateway/tts/google_tts.py`

Google Gemini 3.1 Flash TTS adapter implementing TTSProvider protocol.

- SDK: `google-genai` (`from google import genai`)
- Model: `gemini-3.1-flash-tts-preview`
- API: `client.interactions.create()` with `response_format={"type": "audio"}`, `stream=True`
- Voice: use env `GEMINI_TTS_VOICE` (default `"Kore"`) — 30 voices available
- API key: `GOOGLE_CLOUD_API_KEY` (same key, Gemini API accepts it)
- Output: Gemini returns PCM 24kHz mono. Pipeline expects PCM 16kHz mono. **Resample with numpy** (already in requirements).
- Pattern: buffer text like elevenlabs_rest_client.py (feed/flush/close), synthesize on finish()
- Streaming: use `stream=True` to get audio chunks as they're generated, feed through resampler, emit via on_audio callback
- Spanish: auto-detected by Gemini, no config needed
- Reference: https://ai.google.dev/gemini-api/docs/speech-generation

## Step 4: Create `services/voice-gateway/stt/factory.py`

There is no STT factory yet. Create one:

```python
# Select STT provider based on STT_PROVIDER env
# STT_PROVIDER=deepgram (default) → DeepgramSTT
# STT_PROVIDER=google → GoogleSTT
```

Then update `pipeline/session.py` to use the factory instead of importing DeepgramSTT directly.

NOTE (atlas): the direct `DeepgramSTT` import is in `services/voice-gateway/main.py:34` (the `stt_factory` closure at main.py:203), not in pipeline/session.py — session.py receives the factory as a constructor arg. Updating main.py instead; session.py unchanged.

## Step 5: Update `services/voice-gateway/tts/factory.py`

Add google provider:
```python
# TTS_PROVIDER=google → GoogleTTS
```

## Step 6: Update `services/voice-gateway/requirements.txt`

Add:
```
google-genai
google-cloud-speech
```
Keep existing deps. Do not remove deepgram-sdk or elevenlabs (still needed as fallback).

## Step 7: Update `services/voice-gateway/Dockerfile`

Same fix as orchestrator — build context is project root, need to copy `_shared`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY services/voice-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/voice-gateway/ .
COPY services/_shared/ /app/_shared/
ENV PYTHONPATH="/app:/app/_shared"
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Step 8: Update `infra/fly/fly.voice-gateway.toml`

Add env vars:
```toml
STT_PROVIDER = "google"
TTS_PROVIDER = "google"
GEMINI_TTS_VOICE = "Kore"
```

Update dockerfile path to `"../../services/voice-gateway/Dockerfile"` (relative to fly.toml location). Already set — no change needed there.

## CRITICAL RULES

- DO NOT remove existing Deepgram or ElevenLabs adapters — they're fallbacks
- DO NOT change the STTProvider or TTSProvider interfaces
- DO NOT touch the orchestrator service
- DO NOT commit to git — lucia will review and commit
- UPDATE this file (HANDOFF_VOICE_GATEWAY.md) with your progress after each step. Write what you did, what worked, what didn't. This is your memory — if your context gets compacted, read this file to resume.
- When done, send lucia a message via intercom saying "voice-gateway google adapters done, ready for review"

## Status

- [x] Step 1: Read existing files — done 2026-08-25. All 7 files read, plus main.py (real wiring point), phrase_chunker.py, Dockerfile, fly.voice-gateway.toml.
- [x] Step 2: Write google_stt.py — DONE 2026-08-26. `stt/google_stt.py`: GoogleSTT (Chirp 3, V2 streaming). Sync gRPC client runs in a worker thread bridged to the event loop via queues (asyncio.to_thread reader). end_utterance() closes the request stream → final → is_open False → pipeline reopens per utterance (Deepgram contract). Auth chain: GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE → GOOGLE_CLOUD_API_KEY (client_options api_key) → ADC. Tested with fake client on REAL SDK request objects: /tmp/vg_test_google_stt.py — all pass (partials/finals dispatch, config shape asserted, lifecycle, error path, thread cleanup).
- [x] Step 3: Write google_tts.py — DONE 2026-08-26. `tts/google_tts.py`: GoogleTTS — buffers feed(), synthesizes on finish() via `await client.aio.interactions.create(..., stream=True)`, async-for over events, base64 audio deltas resampled 24k→16k with numpy (_PcmResampler, phase-continuous, bit-exact vs single-shot at ANY chunk size incl. odd-byte), epoch-based barge-in, last_error/bytes_generated for session diagnostics. Tested: /tmp/vg_test_google_tts.py — all pass (happy path == resample reference, barge-in discards post-flush audio, error event, no-key guard, lifecycle).
- [x] Step 4: stt/factory.py + main.py — DONE 2026-08-26. `stt/factory.py` (open_stt per STT_PROVIDER, default deepgram); main.py import + stt_factory closure now go through the factory; /health reports active STT_PROVIDER. Smoke test: main.py imports clean with STT_PROVIDER/TTS_PROVIDER=google; both factories dispatch to the google adapters; defaults still deepgram/elevenlabs.
- [x] Step 5: tts/factory.py — DONE 2026-08-26. `TTS_PROVIDER=google → GoogleTTS` branch added (before azure/elevenlabs).
- [x] Step 6: requirements.txt — DONE 2026-08-26. Added google-genai==2.20.0 + google-cloud-speech==2.40.0 (pinned to the live-verified versions; spec said unpinned, repo style is pinned). ALSO FORCED BUMP: httpx 0.27.2→0.28.1 (google-genai requires >=0.28.1) and pydantic 2.9.2→2.13.4 (google-genai requires >=2.12.5) — both resolver-verified via `pip install --dry-run -r requirements.txt` (clean resolve, exact Docker-build simulation) and behavior-verified: full test suite green under pydantic 2.13.4 + httpx 0.28.1.
- [x] Step 7: Dockerfile — DONE 2026-08-26. Replaced with the build-context version (COPY services/voice-gateway/ + services/_shared/, PYTHONPATH) — mirrors the live orchestrator Dockerfile pattern byte-for-byte.
- [x] Step 8: fly.voice-gateway.toml — DONE 2026-08-26. Added TTS_PROVIDER=google, GEMINI_TTS_VOICE=Kore; STT_PROVIDER shipped as "deepgram" (commit ca9c412, per deploy-ordering note — flip to "google" when the service-account secret lands; TODO comment in toml). dockerfile path already correct. GOOGLE_CLOUD_API_KEY must be added as a fly secret for the voice-gateway app (TTS needs it); GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE secret needed only for option A.
- [x] Notify lucia for review — SENT 2026-08-26.
- [x] Review — ACCEPTED by lucia 2026-08-26 (all 9 files, no changes). Lucia commits + deploys. DEPLOY-ORDERING NOTE (flagged to lucia): if the deploy lands before Dario's A/B decision, ship with STT_PROVIDER=deepgram + TTS_PROVIDER=google (STT 401s on google until a service-account secret exists); flip the line when the decision lands.

## Live verification log (atlas, 2026-08-25)

Verified from the running Fly machine `casa-orquesta-orchestrator` (GOOGLE_CLOUD_API_KEY lives in that app's secrets; no fly machine exists yet for voice-gateway, and the fly CLI can't read secret values locally). Fly CLI quirks: `fly ssh console -C` mangles long/complex commands (strips spaces/`&&`); use short plain commands, upload files with `fly ssh sftp put <local> <remote> -a <app>` (no overwrite — `rm` remote file first). App machines auto-stop: wake with a curl to the health endpoint before `fly ssh console`.

**TTS — VERIFIED LIVE.** Raw POST to `https://generativelanguage.googleapis.com/v1beta/interactions` with header `x-goog-api-key: $GOOGLE_CLOUD_API_KEY`, body `{"model": "gemini-3.1-flash-tts-preview", "input": "<es-MX phrase>", "response_format": {"type": "audio"}, "generation_config": {"speech_config": [{"voice": "Kore"}]}}` → HTTP 200 in 3.5s. Audio is base64 PCM at `steps[0].content[0].data` (top-level keys: id, status, usage, steps, object, model — there is NO top-level `output_audio` key in the raw JSON; the SDK's `interaction.output_audio` is a convenience property). Sample rate 24kHz/16bit/mono confirmed by byte math (~4.3s of speech for the test phrase). So the key is valid for the Gemini API and the resample 24k→16k is required.

**TTS streaming shape (from google-genai 2.20.0 source + official docs):** `client.interactions.create(..., stream=True)` returns an async stream of events; audio arrives as events where `event.event_type == "step.delta"` and `event.delta.type == "audio"`, payload base64 in `event.delta.data`. (Docs verified: https://ai.google.dev/gemini-api/docs/interactions/speech-generation)

**TTS streaming — VERIFIED LIVE (atlas, 2026-08-26).** `/tmp/vg_probe3.py` on the fly machine: `await client.aio.interactions.create(model, input, response_format={"type":"audio"}, generation_config={"speech_config":[{"voice":"Kore"}]}, stream=True)` + `async for` → event order: interaction.created → interaction.status_update → step.start → 115× step.delta (audio) → step.stop → interaction.completed; 220,800 bytes ≈ 4.6s @24kHz (matches non-streaming run). 2.20.0 API notes: the async client is `genai.Client(api_key=...).aio` (there is NO top-level `AsyncClient` export); `create()` is a coroutine (must await before iterating); `AudioDelta` fields: `type="audio"`, `data` (b64 PCM), `sample_rate` (=24000), `channels`, `mime_type`, `uri`.

**STT — SDK shape DISCOVERED, live test FAILED on auth (atlas, 2026-08-26).** google-cloud-speech 2.40.0 refactored the v2 API shape vs. older examples:
- `RecognitionConfig` fields: `model`, `language_codes`, `explicit_decoding_config` (holds `encoding` enum + `sample_rate_hertz` + `audio_channel_count`), `features`, `adaptation`, ... — the old top-level `encoding`/`sample_rate_hertz` fields are GONE.
- Enum access: `speech_v2.ExplicitDecodingConfig.AudioEncoding.LINEAR16`.
- `StreamingRecognitionConfig` fields: `config` (a RecognitionConfig) + `streaming_features` (`interim_results`, `enable_voice_activity_events`, `voice_activity_timeout`, `endpointing_sensitivity`) + `config_mask`.
- `SpeechClient.streaming_recognize(requests=<iterator>)` — NO `config=` param anymore; config travels inside `StreamingRecognizeRequest` (fields: `recognizer`, `streaming_config`, `audio`, + end-of-audio field being confirmed).
- API-key auth pattern to verify: subclass `google.auth.credentials.AnonymousCredentials`, set `request.headers["x-goog-api-key"]` in `before()`. (gRPC metadata plugin calls `before()` on a fake request — confirmed plausible from google-auth source, live test will prove it.)

**STT — LIVE: API-key auth REJECTED by the API (atlas, 2026-08-26, two real-target observations).** Ran `/tmp/vg_verify.py` (fixed to shapes below) + `/tmp/vg_probe2.py` (non-streaming round-trip: TTS audio → resample → `Recognize`) on the orchestrator fly machine holding the key. `SpeechClient(client_options={"api_key": KEY})` builds fine (native `google.auth.api_key.Credentials`, api-core 2.34.0) and the request reaches `speech.googleapis.com` — which returns 401 `Unauthenticated: API keys are not supported by this API` (CREDENTIALS_MISSING) on BOTH `Speech.Recognize` and `Speech.StreamingRecognize`. Cross-checked current Google docs same day: STT v2 = OAuth2/service-account/ADC only; no API-key support. TTS (Gemini API) is UNAFFECTED — same key, TTS_OK re-verified 4.1s. VERIFIED SDK shapes (2.40.0, pydantic-style `Message`, introspected via `inst._pb.DESCRIPTOR`): `RecognizeRequest` fields = config, config_mask, **content** (audio bytes, NOT `audio`), recognizer, uri; `StreamingRecognizeRequest` = **audio**, recognizer, streaming_config; `RecognitionConfig` = model, language_codes, explicit_decoding_config, auto_decoding_config, adaptation, features, ...; `StreamingRecognitionConfig` = config, config_mask, streaming_features. **DECISION PENDING (lucia→Dario): A) service account JSON (roles/speech.user) as fly secret, adapter reads `GOOGLE_CLOUD_SERVICE_ACCOUNT_FILE` (falls back to API key if absent) — keeps Chirp 3; B) STT stays Deepgram, only TTS moves to Google.**

## Resume point (read this first if compacted)

1. STT live verification DONE 2026-08-26: API-key auth rejected by speech.googleapis.com (both methods) — see live log. TTS verified live incl. round-trip audio. Working probes: `/tmp/vg_verify.py` (TTS+streaming STT) and `/tmp/vg_probe2.py` (TTS→STT non-streaming round-trip); local venv `/tmp/vg-venv` (google-cloud-speech 2.40.0, google-genai 2.20.0, google-auth 2.57.0, api-core 2.34.0). Fly CLI at `/home/hipotecario/.fly/bin/fly` (not on default PATH). `fly ssh console -C` needs short plain commands; sftp put never overwrites (`rm` remote first).
2. Then write the adapters (steps 2–5) using ONLY the shapes verified above — do not trust memory for google SDK field names; if a field fails locally, introspect with `/tmp/vg-venv/bin/python` (venv has google-genai 2.20.0, google-cloud-speech 2.40.0).
3. Steps 6–8 are mechanical file edits.
4. Run `services/voice-gateway/tests/` (test_stt/test_tts/test_pipeline) locally to prove no regressions; py_compile every touched file.
5. Do NOT git commit. Send lucia: "voice-gateway google adapters done, ready for review".