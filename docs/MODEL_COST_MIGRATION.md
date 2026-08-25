# Plan — Migración de modelos a Fireworks AI

**Estado:** decisión actualizada (2026-08-25): orquestador → **MiniMax M3** ($0.30/$1.20 por M), dialog → **DeepSeek V4 Flash** ($0.22/$0.66 por M). Verificado en vivo con tool use + es-MX. Ruta técnica confirmada contra docs de Fireworks.
**Principio:** el cambio de modelo es **casi gratis en código** — todo está detrás de env vars. El trabajo real es conectar el proveedor, correr evals y validar calidad/latencia es-MX.

## Estado actual (verificado en código)

| Rol | Agente | Modelo actual | Env var |
|-----|--------|---------------|---------|
| Orquestador (conversación principal) | `realestate.py` | `accounts/fireworks/models/minimax-m3` | `ANTHROPIC_MODEL_ORCHESTRATOR` |
| Diálogo de soporte (búsqueda + auditoría) | `locator.py`, `audit.py` | `accounts/fireworks/models/deepseek-v4-flash-0731` | `ANTHROPIC_MODEL_DIALOG` |

- Precios usados por spend caps: M3 $0.30/$1.20 por M, Flash $0.22/$0.66 por M (verificado en vivo 2026-08-25).
- El harness real es `agents/__init__.py::_run_real` (NO harness.py — ese es esqueleto orphaned). El tool-use loop ya funciona contra Fireworks. Validado con live test.
- Los modelos viven en `agents/*.py` por env vars con defaults de Fireworks.

## Decisión y ruta técnica (verificada en vivo, 2026-08-23)

**Fireworks expone un endpoint compatible con la API de Anthropic** (`POST /v1/messages`), lo que permite que el Claude Agent SDK apunte a Fireworks sin reescribir el harness.

Cómo conectar:

```bash
# .env
ANTHROPIC_BASE_URL=https://api.fireworks.ai/inference
ANTHROPIC_API_KEY=<fireworks_api_key>        # no es la key de Anthropic
ANTHROPIC_MODEL_ORCHESTRATOR=accounts/fireworks/models/deepseek-v4-flash-0731
ANTHROPIC_MODEL_DIALOG=accounts/fireworks/models/deepseek-v4-flash-0731
```

Detalles confirmados de la doc de Fireworks:
- Endpoint Anthropic soportado: `/v1/messages`, streaming y no-streaming.
- `model` debe ser el resource name de Fireworks (ej. `accounts/fireworks/models/deepseek-v4-flash-0731`), no un nombre de Claude.
- `max_tokens` opcional; el header `anthropic-version` se ignora.
- Serverless y on-demand; **requiere pasar por `api.fireworks.ai/inference`**.
- No soportado: server tools de Anthropic, `cache_control`, `eager_input_streaming` — el harness no los usa hoy.

**Requisito:** una API key de Fireworks (la del town hipotecario no es de casa-orquesta; este repo necesitará la suya).

## Pendientes antes de tocar stage

1. **Confirmar precio de deepseek-v4-flash-0731** en Fireworks (en vivo) y actualizar `hooks/spend_caps.py` con su tarifa — hoy solo conoce sonnet/haiku/opus.
2. **Correr `make eval`** con el swap por env var (evals de routing + guardrails) — requiere la Fase 1 del harness operativa, o al menos el test contract de `test_agents.py`.
3. **Spot-check es-MX en vivo** (5–10 preguntas tipo tester): latencia P50/P95 (presupuesto ≤1.5s/≤2.5s) y calidad percibida de voz.
4. **Regla del repo (HANDOFF):** cualquier cosa >$20/mes de runtime pasa por el fundador; el cambio de modelo es esa categoría — ya está aprobado por Dario.

## Pasos de ejecución

- [x] Obtener API key de Fireworks para casa-orquesta (Dario/Paco).
- [x] Confirmar tarifas en vivo → actualizar `spend_caps.py` (M3, Glimmer, V4 Pro, V4 Flash).
- [x] Cambiar defaults: orquestador → MiniMax M3, dialog → DeepSeek V4 Flash, en `agents/*.py`, `main.py`, `.env.example`, `.env`, `fly.orchestrator.toml`.
- [x] Live test: tool use + usage + es-MX validado contra Fireworks (M3 3/3 casos, Glimmer 1/3).
- [x] Fixes de review (atlas): over-bill fallback, stale defaults, zero-usage guard.
- [ ] `make eval` + spot-check es-MX (161 assertions contract).
- [ ] Refactor streaming: first-call stream (elimina double-billing de _stream_final_text).
- [ ] Deploy a Fly.io (pendiente credenciales de Paco).

## Archivos relevantes

- `services/orchestrator/agents/realestate.py`, `locator.py`, `audit.py`
- `services/orchestrator/agents/__init__.py` (real AI path: `_run_real`)
- `services/orchestrator/hooks/spend_caps.py`
- `services/orchestrator/main.py`
- `.env.example`, `infra/fly/fly.orchestrator.toml`
- `docs/ARCHITECTURE.md` (cost shape), `docs/HANDOFF.md` (regla >$20/mes), `evals/`
