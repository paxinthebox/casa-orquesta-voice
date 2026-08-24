# Plan — Migración de modelos a deepseek v4 flash

**Estado:** decisión de Dario (2026-08-23): usar **deepseek v4 flash**. Ruta técnica verificada en vivo (docs de Fireworks).
**Principio:** el cambio de modelo es **casi gratis en código** — todo está detrás de env vars. El trabajo real es conectar el proveedor, correr evals y validar calidad/latencia es-MX.

## Estado actual (verificado en código)

| Rol | Agente | Modelo actual | Env var |
|-----|--------|---------------|---------|
| Orquestador (conversación principal) | `realestate.py` | `claude-sonnet-4-6` | `ANTHROPIC_MODEL_ORCHESTRATOR` |
| Diálogo de soporte (búsqueda + auditoría) | `locator.py`, `audit.py` | `claude-haiku-4-5-20251001` | `ANTHROPIC_MODEL_DIALOG` |

- Precios usados por spend caps: sonnet-4-6 $3.00/$15.00 por M, haiku-4-5 $0.80/$4.00 por M.
- El harness es **Claude Agent SDK** (`harness.py`) — ojo: hoy es esqueleto; `run_stream` aún lanza `NotImplementedError` (Phase 1 de PLAN.md). La migración de modelo se hace al construir la Fase 1.
- Los modelos viven en `agents/*.py` por env vars con default Anthropic.

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

- [ ] Obtener API key de Fireworks para casa-orquesta (Dario/Paco).
- [ ] Confirmar tarifa de deepseek-v4-flash-0731 en vivo → actualizar `spend_caps.py`.
- [ ] Cambiar defaults en `agents/realestate.py`, `locator.py`, `audit.py` y `.env.example` al resource name de Fireworks, con `ANTHROPIC_BASE_URL`.
- [ ] Implementar Fase 1 del harness (wiring real del SDK) ya apuntando a Fireworks.
- [ ] `make eval` + spot-check es-MX → decidir si el orquestador también baja (o mantener sonnet solo ahí).

## Archivos relevantes

- `services/orchestrator/agents/realestate.py`, `locator.py`, `audit.py`
- `services/orchestrator/hooks/spend_caps.py`
- `services/orchestrator/harness.py`
- `.env.example`
- `docs/ARCHITECTURE.md` (cost shape), `docs/HANDOFF.md` (regla >$20/mes), `evals/`
