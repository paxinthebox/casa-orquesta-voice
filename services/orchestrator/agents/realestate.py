"""
realestate_agent — top-level orchestrator. Composes locator_agent and
audit_agent as sub-agents (exposed to itself as tools per the
"agents-as-tools" pattern).

Phase 1.6 status: real `_agent_as_tool` handler delegates by recursively
calling the child agent's `run()`.

Phase 5.3 status: the child context is now **isolated** — the child
receives a deep copy of the parent's state dict and a fresh trace
list. Writes the child makes to its state do not propagate back unless
the child explicitly returns `state_delta` (default empty), and the
parent's trace records the sub-agent invocation as a single
`subagent_run` step rather than absorbing the child's tool-by-tool
churn. Live SSE consumers still see every child event via the
trace_hook fanout (RunContext.emit always forwards to subscribers);
only the parent's *recorded* trace becomes summarized.
"""
from __future__ import annotations

import copy
import os
import time

from . import Agent, RunContext, Tool, TraceStep
from .locator import locator_agent
from .audit import audit_agent
from .guardrails import orchestrator_system, idle_orchestrator_reply

MODEL_ORCHESTRATOR = os.getenv("ANTHROPIC_MODEL_ORCHESTRATOR", "accounts/fireworks/models/minimax-m3")


def _agent_as_tool(child: Agent) -> Tool:
    """Wrap a sub-Agent as a Tool the orchestrator can invoke.

    P5.3: child gets a deep-copied state + fresh trace list. After the
    child returns, the parent merges an optional `state_delta` from the
    child's result back into parent state, then appends a single
    `subagent_run` summary step to its own trace.
    """
    async def handler(args: dict, ctx) -> dict:
        sub_msg = args.get("message", "")
        from people_intent import implies_people_search

        intent_msg = str(ctx.state.get("user_query") or sub_msg)
        if implies_people_search(intent_msg):
            people_filters = ctx.state.get("people_filters") or {}
            intent_msg = str(ctx.state.get("user_query") or sub_msg)
            if people_filters:
                from reply_synth import describe_criteria

                sub_msg = (
                    f"{intent_msg}\n\n"
                    f"Usa estos criterios para find_brokers o find_collaborator_agents "
                    f"(no uses search_listings): {describe_criteria(people_filters)}."
                )
        else:
            filters = ctx.state.get("filters") or {}
            if filters:
                from reply_synth import describe_criteria

                sub_msg = (
                    f"{sub_msg}\n\n"
                    f"Usa estos criterios del usuario en tus herramientas: {describe_criteria(filters)}."
                )
        # Parent-side state writes: the supervisor's focus pins are
        # set BEFORE the child runs so the deep-copied state the child
        # receives carries the focus through.
        if args.get("focus_listing_id"):
            ctx.state["focus_listing_id"] = args["focus_listing_id"]
        if args.get("focus_document_id"):
            ctx.state["focus_document_id"] = args["focus_document_id"]

        # P5.3 isolation: deep-copy state, fresh trace. The shared
        # `wire` sink still receives every child event so the run-level
        # trace returned to SSE consumers stays complete.
        sub_ctx = RunContext(
            run_id=ctx.run_id,
            trace=[],                                 # P5.3: own trace
            depth=ctx.depth + 1,
            started_at_ms=ctx.started_at_ms,
            state=copy.deepcopy(ctx.state),           # P5.3: own state copy
            wire=ctx.wire,
        )
        result = await child.run(sub_msg, sub_ctx)

        # P5.3: explicit propagation only — the child returns
        # `state_delta` for any keys it wants to push back to the
        # parent. Default is empty; no surprise leaks.
        state_delta = result.get("state_delta") if isinstance(result, dict) else None
        if isinstance(state_delta, dict):
            ctx.state.update(state_delta)

        # P5.3: record a single summary step in the parent's trace so
        # the parent's view of the run stays clean. The sub-agent's
        # detailed events were already forwarded to SSE subscribers
        # live via trace_hook.emit() from inside the child.
        summary_step = TraceStep(
            kind="subagent_run",
            agent=child.name,
            detail={
                "output_preview": (result.get("output") or "")[:280],
                "sub_trace_count": len(sub_ctx.trace),
                "state_delta_keys": sorted((state_delta or {}).keys()),
            },
            ts_ms=int(time.time() * 1000) - ctx.started_at_ms,
        )
        ctx.trace.append(summary_step)
        if ctx.wire is not None:
            ctx.wire.append(summary_step)
        return result

    return Tool(
        name=child.name,
        description=f"Sub-agente. {child.description}",
        input_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "Instrucción al sub-agente en lenguaje natural (es-MX)."
                    ),
                },
                "focus_listing_id": {
                    "type": "string",
                    "description": "Listing relevante para enfocar al sub-agente.",
                },
                "focus_document_id": {
                    "type": "string",
                    "description": "Document ID relevante (para audit_agent).",
                },
            },
            "required": ["message"],
        },
        handler=handler,
    )


realestate_agent = Agent(
    name="realestate_agent",
    description=(
        "Orquestador de Casa·Orquesta. Coordina sub-agentes locator y audit, "
        "y conversa con compradores y vendedores."
    ),
    system=orchestrator_system(
        "Eres realestate_agent, el orquestador de Casa·Orquesta. Operas en "
        "CDMX y Morelos. El rol del cliente (client_role) indica si es "
        "comprador (buyer) o vendedor (seller).\n"
        "Tienes dos sub-agentes como herramientas:\n"
        "  - locator_agent: úsalo para buscar, comparar o detallar propiedades "
        "    (venta o renta anual: departamentos, casas, condominios), "
        "    compradores, agentes colaboradores y brokers.\n"
        "  - audit_agent: úsalo cuando se requiera revisar un contrato o "
        "    validar un inmueble en registros públicos.\n"
        "Reglas:\n"
        "  1. Si la intención es buscar/recomendar/comparar propiedades, "
        "compradores, agentes colaboradores o brokers → delega a locator_agent.\n"
        "  1b. Si client_role=seller y habla de publicar, valuar, comparar "
        "precios, ofertas o encontrar compradores → delega a locator_agent.\n"
        "  2. Si hay una promesa lista para firmar, o pregunta "
        "'¿es legal?', '¿está a nombre del dueño?', '¿tiene gravámenes?', "
        "'¿impuestos al corriente?' → delega a audit_agent.\n"
        "  3. Si la pregunta es general o conversacional, responde tú mismo "
        "en es-MX (2-3 oraciones) y sugiere el siguiente paso concreto.\n"
        "  4. Tras delegar a locator_agent, responde en es-MX repitiendo "
        "los criterios del usuario (tipo, ciudad, estado, recámaras, presupuesto) "
        "y resume solo las propiedades que devolvió la herramienta — no inventes.\n"
        "  5. Cumple LFPDPPP: no compartas datos personales sin consentimiento."
    ),
    tools=[
        _agent_as_tool(locator_agent),
        _agent_as_tool(audit_agent),
    ],
    model=MODEL_ORCHESTRATOR,
)


# ---------------- Simulated router ----------------
def _implies_property_search(msg: str, filters: dict) -> bool:
    from people_intent import implies_people_search

    if implies_people_search(msg):
        return False
    if (
        filters.get("state") or filters.get("type") or filters.get("city")
        or filters.get("listing_mode")
    ):
        return True
    return any(
        k in msg
        for k in [
            "busco", "necesito", "quiero", "buscar", "encuentra", "encontrar",
            "muestra", "mejores opciones", "comparar", "departamento", "depa",
            "depto", "apartamento", "condominio", "condo", "casa", "loft",
            "estudio", "penthouse", "recámaras", "habitaciones", "recamaras",
            "presupuesto", "alberca", "propiedad", "propiedades", "inmueble",
            "renta", "alquiler", "arrendar", "venta", "comprar",
            "cuernavaca", "morelos", "cdmx",
        ]
    )


def _implies_seller_intent(msg: str) -> bool:
    return any(
        k in msg
        for k in [
            "vender", "vendo", "publicar", "publicación", "publicacion",
            "oferta", "ofertas", "valuación", "valuacion", "valuar",
            "precio de venta", "listar", "mi propiedad", "mis propiedades",
            "compradores interesados",
        ]
    )


async def _realestate_simulated(self, message: str, ctx: RunContext) -> dict:
    """Deterministic router based on intent keywords — same trace shape as real Claude."""
    from people_intent import classify_people_kind, implies_people_search
    from agents.guardrails import person_follow_up_reply
    from reply_synth import format_supervisor_search_reply

    msg = message.lower()
    filters = ctx.state.get("filters") or {}
    people_filters = ctx.state.get("people_filters") or filters
    client_role = ctx.state.get("client_role", "buyer")

    focus_person_id = str(ctx.state.get("focus_person_id") or "")
    if focus_person_id:
        follow_up = person_follow_up_reply(
            message,
            person_id=focus_person_id,
            person_name=str(ctx.state.get("focus_person_name") or focus_person_id),
            person_kind=str(ctx.state.get("focus_person_kind") or "collaborator"),
        )
        if follow_up:
            return {"output": follow_up, "data": None}

    needs_audit = any(k in msg for k in [
        "revisar", "auditar", "audit", "gravamen", "gravámenes", "predial",
        "rpp", "registro público", "catastro", "legal", "inegi",
        "rfc", "sat", "lfpdppp", "nom-151", "nom 151", "promesa",
        "contrato", "listo para firmar",
    ])
    needs_locator = (
        implies_people_search(message)
        or _implies_property_search(msg, filters)
        or _implies_seller_intent(msg)
    )

    if needs_audit:
        sub_tool = self.tool_map().get("audit_agent")
        assert sub_tool is not None, "audit_agent tool missing from registry"
        args = {"message": message,
                "focus_listing_id": ctx.state.get("focus_listing_id"),
                "focus_document_id": ctx.state.get("focus_document_id")}
        ctx.emit("agent_tool", self.name, {"tool": "audit_agent", "args": args})
        sub = await sub_tool.handler(args, ctx)
        return {"output": f"[Auditoría] {sub.get('output', '')}", "data": sub.get("data")}

    if needs_locator:
        sub_tool = self.tool_map().get("locator_agent")
        assert sub_tool is not None, "locator_agent tool missing from registry"
        args = {"message": message}
        ctx.emit("agent_tool", self.name, {"tool": "locator_agent", "args": args})
        sub = await sub_tool.handler(args, ctx)
        data = sub.get("data") or {}
        out: dict = {
            "output": format_supervisor_search_reply(
                filters=people_filters if implies_people_search(message) else filters,
                sub_output=str(sub.get("output") or ""),
                data=data if isinstance(data, dict) else None,
                people_kind=classify_people_kind(message),
            ),
            "data": data,
        }
        if sub.get("state_delta"):
            out["state_delta"] = sub["state_delta"]
        return out

    return {"output": idle_orchestrator_reply(client_role), "data": None}


realestate_agent._run_simulated = _realestate_simulated.__get__(realestate_agent, Agent)  # type: ignore
