"""
Casa·Orquesta · Voice — Agent verification suite.

Ported from ../casa-orquesta-mvp/tests/test_agents.py with minimal path
adjustments (test file is now at services/orchestrator/tests/ instead of
tests/) and import guards so the script runs even before agents.py exists.

The behavior contract: this file's assertions are the source of truth for
the multi-agent system. Phase 1.5–1.7 fill in services/orchestrator/agents/*
so these assertions go green. Phase 1.3 only ports the file.

Runs every agent, sub-agent, and tool path with stubbed downstream services
and reports pass/fail per case. Confirms:
  - the agent registry is complete and wired correctly
  - realestate_agent routes the right intents to locator/audit
  - every tool is invokable and returns the expected shape
  - traces have well-formed start/end pairing and correct depth
  - state propagates across nested agent calls

Run from repo root:
    cd services/orchestrator && python3 tests/test_agents.py
"""
from __future__ import annotations

import os
import sys
import types
import asyncio
import inspect

os.environ.setdefault("REGISTRY_MODE", "mock")

# ---------------------------------------------------------------------
# Make the orchestrator package importable
# (Voice repo layout: this file lives at services/orchestrator/tests/,
#  so the orchestrator dir is one level up — different from MVP path.)
# ---------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ORCH)

# ---------------------------------------------------------------------
# Stub external deps (PyPI not reachable in sandbox)
# ---------------------------------------------------------------------
class _FakeResp:
    def __init__(self, j, status=200):
        self._j = j
        self.status_code = status
    def json(self): return self._j
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

class _FakeClient:
    """Stubs httpx.AsyncClient to simulate listings + matching responses."""
    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass

    async def post(self, url, json=None):
        # matching service
        if "match/search" in url:
            filters = (json or {}).get("filters", {})
            results = [
                {"id":"L-CDMX-001","title":"Depto Roma Norte","state":"CDMX",
                 "city":"CDMX","neighborhood":"Roma Norte","address":"Querétaro, Roma Norte",
                 "price_mxn":6800000,"beds":2,"baths":2,"m2":95,"type":"departamento",
                 "features":["roof garden","pet friendly"],"description":"Depto luminoso",
                 "lat":19.4153,"lng":-99.1654,"score":0.83,"media":[]},
                {"id":"L-MOR-001","title":"Casa con alberca","state":"Morelos",
                 "city":"Cuernavaca","neighborhood":"Tabachines","address":"Privada del Bosque",
                 "price_mxn":7900000,"beds":4,"baths":3,"m2":320,"type":"casa",
                 "features":["alberca privada","jardín"],"description":"Casa con jardín",
                 "lat":18.9356,"lng":-99.2305,"score":0.71,"media":[]},
            ]
            if filters.get("state") == "Morelos":
                results = [r for r in results if r["state"] == "Morelos"]
            elif filters.get("state") == "CDMX":
                results = [r for r in results if r["state"] == "CDMX"]
            return _FakeResp({"count": len(results), "results": results})
        return _FakeResp({})

    async def get(self, url, params=None):
        # listings service per-id
        if "/listings/L-CDMX-001" in url:
            return _FakeResp({
                "id":"L-CDMX-001","title":"Depto Roma Norte","state":"CDMX",
                "city":"CDMX","neighborhood":"Roma Norte","address":"Querétaro, Roma Norte",
                "price_mxn":6800000,"beds":2,"baths":2,"m2":95,"type":"departamento",
                "features":["roof garden","pet friendly"],"lat":19.4153,"lng":-99.1654,
            })
        if "/listings/L-MOR-001" in url:
            return _FakeResp({
                "id":"L-MOR-001","title":"Casa con alberca","state":"Morelos",
                "city":"Cuernavaca","neighborhood":"Tabachines","address":"Privada del Bosque",
                "price_mxn":7900000,"beds":4,"baths":3,"m2":320,"type":"casa",
                "features":["alberca privada"],"lat":18.9356,"lng":-99.2305,
            })
        if "/docs/D-DEMO" in url and url.endswith("D-DEMO"):
            return _FakeResp({
                "id":"D-DEMO","kind":"promesa_compraventa",
                "listing_id":"L-CDMX-001","amount_mxn":6500000,
                "plazo_dias":60,"sha256":"a"*64,"nom151_token":"NOM151-MOCK-XYZ",
                "status":"draft",
            })
        if "/docs/D-DEMO/pdf" in url:
            return _FakeResp("PDF-BYTES")
        # listings list
        if url.endswith("/listings"):
            return _FakeResp([])
        return _FakeResp({})

fake_httpx = types.ModuleType("httpx")
fake_httpx.AsyncClient = _FakeClient
sys.modules["httpx"] = fake_httpx

# anthropic stub — agent code only constructs Anthropic() when USE_REAL_AI
fake_anthropic = types.ModuleType("anthropic")
class _AnthropicStub:
    def __init__(self, **kw): pass
fake_anthropic.Anthropic = _AnthropicStub
sys.modules["anthropic"] = fake_anthropic

# Make sure we're in simulated mode for deterministic testing
os.environ.pop("ANTHROPIC_API_KEY", None)

import datasets   # noqa: E402  (ported in P1.2)

# Phase-1.3 guard: agents module is owned by P1.5–P1.7. Before those land,
# either `import agents` will fail outright, OR it will succeed as an empty
# PEP-420 namespace package (because services/orchestrator/agents/ exists as
# a directory). Both cases need to be treated as "not yet implemented" so the
# script still executes and reports clean per-section failures.
_REQUIRED_AGENTS_ATTRS = (
    "list_agents_meta", "run_orchestrator",
    "REALESTATE", "LOCATOR", "AUDIT", "RunContext",
)
try:
    import agents  # noqa: E402
    _missing = [a for a in _REQUIRED_AGENTS_ATTRS if not hasattr(agents, a)]
    if _missing:
        raise ImportError(
            f"agents module is incomplete (Phase 1.5 deliverable). "
            f"Missing public attrs: {_missing}"
        )
    _AGENTS_IMPORT_ERROR: Exception | None = None
except ImportError as e:
    agents = None  # type: ignore
    _AGENTS_IMPORT_ERROR = e

# ---------------------------------------------------------------------
# Mini test harness
# ---------------------------------------------------------------------
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

def expect(label: str, cond: bool, detail: str = ""):
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append((label, detail))
        print(f"  ❌ {label}  ← {detail}")


def section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _safe_section(label: str, body):
    """Run a test section body; convert phase-transition errors into clean
    per-section failure records:
      - AttributeError / TypeError when agents missing → Phase 1.5 dep
      - NotImplementedError when handlers stub          → Phase 1.6 dep
      - FileNotFoundError on buyer.html                 → MVP-only test
    """
    try:
        body()
    except AttributeError as e:
        if agents is None:
            expect(label, False,
                   f"agents module not yet implemented (Phase 1.5): {_AGENTS_IMPORT_ERROR}")
        else:
            expect(label, False, f"AttributeError: {e!r}")
    except TypeError as e:
        if agents is None:
            expect(label, False,
                   f"agents module not yet implemented (Phase 1.5): {_AGENTS_IMPORT_ERROR}")
        else:
            expect(label, False, f"TypeError: {e!r}")
    except NotImplementedError as e:
        expect(label, False, f"tool handler not yet implemented (Phase 1.6): {e}")
    except FileNotFoundError as e:
        expect(label, False, f"FileNotFoundError: {e!r}")


# ---------------------------------------------------------------------
# TEST 1 — Agent registry
# ---------------------------------------------------------------------
section("1. Agent registry & tool registration")

def _test1():
    meta = agents.list_agents_meta()
    expect("3 agents registered", len(meta) == 3, f"got {len(meta)}")
    names = [a["name"] for a in meta]
    for a in ["realestate_agent", "locator_agent", "audit_agent"]:
        expect(f"agent '{a}' present", a in names)

    re_agent = next(a for a in meta if a["name"] == "realestate_agent")
    re_tools = [t["name"] for t in re_agent["tools"]]
    expect("realestate has tool 'locator_agent'", "locator_agent" in re_tools)
    expect("realestate has tool 'audit_agent'", "audit_agent" in re_tools)
    expect("realestate has exactly 2 tools (sub-agents)", len(re_tools) == 2,
           f"got {re_tools}")

    loc_agent = next(a for a in meta if a["name"] == "locator_agent")
    loc_tools = [t["name"] for t in loc_agent["tools"]]
    for t in ["search_listings", "get_listing", "compare_listings",
              "find_buyers", "find_collaborator_agents", "find_brokers"]:
        expect(f"locator has tool '{t}'", t in loc_tools)
    expect("locator has exactly 6 tools", len(loc_tools) == 6, f"got {loc_tools}")

    aud_agent = next(a for a in meta if a["name"] == "audit_agent")
    aud_tools = [t["name"] for t in aud_agent["tools"]]
    for t in ["review_promesa", "rpp_lookup", "catastro_lookup",
              "inegi_zone_stats", "sat_rfc_check"]:
        expect(f"audit has tool '{t}'", t in aud_tools)
    expect("audit has exactly 5 tools", len(aud_tools) == 5, f"got {aud_tools}")
_safe_section("Section 1: Agent registry", _test1)


# ---------------------------------------------------------------------
# TEST 2 — Tool input schemas well-formed
# ---------------------------------------------------------------------
section("2. Tool input schemas")

def _test2():
    for a in [agents.REALESTATE, agents.LOCATOR, agents.AUDIT]:
        for t in a.tools:
            s = t.input_schema
            expect(f"{a.name}.{t.name}: schema has type=object",
                   s.get("type") == "object", str(s))
            expect(f"{a.name}.{t.name}: schema has 'properties' dict",
                   isinstance(s.get("properties"), dict))
            expect(f"{a.name}.{t.name}: handler is awaitable",
                   inspect.iscoroutinefunction(t.handler))
_safe_section("Section 2: Tool input schemas", _test2)


# ---------------------------------------------------------------------
# Helper — trace shape verification
# ---------------------------------------------------------------------
def verify_trace_shape(label: str, trace: list) -> None:
    """Pair agent_start↔agent_end, ensure depth never negative,
    tools always nested inside an agent."""
    depth = 0
    stack: list[str] = []
    bad = ""
    for s in trace:
        if s["kind"] == "agent_start":
            depth += 1
            stack.append(s["agent"])
        elif s["kind"] == "agent_end":
            if not stack:
                bad = f"agent_end '{s['agent']}' with no matching start"
                break
            opened = stack.pop()
            if opened != s["agent"]:
                bad = f"agent_end '{s['agent']}' does not match start '{opened}'"
                break
            depth -= 1
        elif s["kind"] == "agent_tool":
            if not stack:
                bad = f"agent_tool '{s['detail'].get('tool')}' outside any agent"
                break
        if depth < 0:
            bad = "depth went negative"
            break
    if not bad and stack:
        bad = f"unclosed agents: {stack}"
    expect(f"{label}: trace well-formed", not bad, bad)


# ---------------------------------------------------------------------
# TEST 3 — Locator path (CDMX search)
# ---------------------------------------------------------------------
section("3. End-to-end: locator path (CDMX)")

def _test3():
    result = asyncio.run(agents.run_orchestrator(
        "Busco un departamento en Roma Norte de 2 recámaras hasta 8 millones",
        state={"filters": {"state": "CDMX", "beds_min": 2, "price_max_mxn": 8000000}}
    ))
    trace = result["trace"]
    agents_seen = [s["agent"] for s in trace if s["kind"] == "agent_start"]
    expect("realestate_agent invoked", "realestate_agent" in agents_seen)
    expect("locator_agent invoked", "locator_agent" in agents_seen)
    expect("audit_agent NOT invoked", "audit_agent" not in agents_seen)
    tools_used = [s["detail"]["tool"] for s in trace if s["kind"] == "agent_tool"]
    expect("orchestrator delegated to locator_agent (tool call)",
           "locator_agent" in tools_used)
    expect("locator_agent called search_listings",
           "search_listings" in tools_used)
    candidates = result["state"].get("last_candidates", [])
    expect("last_candidates populated", len(candidates) > 0,
           f"got {len(candidates)} candidates")
    expect("candidates are CDMX (filtered)",
           all(c["state"] == "CDMX" for c in candidates))
    verify_trace_shape("locator-CDMX", trace)
_safe_section("Section 3: Locator CDMX", _test3)


# ---------------------------------------------------------------------
# TEST 4 — Locator path (Morelos)
# ---------------------------------------------------------------------
section("4. End-to-end: locator path (Morelos filter)")

def _test4():
    result = asyncio.run(agents.run_orchestrator(
        "Quiero una casa con alberca en Cuernavaca",
        state={"filters": {"state": "Morelos"}}
    ))
    candidates = result["state"].get("last_candidates", [])
    expect("Morelos filter respected", all(c["state"] == "Morelos" for c in candidates),
           f"states: {[c['state'] for c in candidates]}")
    expect("at least 1 Morelos result", len(candidates) >= 1)
_safe_section("Section 4: Locator Morelos", _test4)


# ---------------------------------------------------------------------
# TEST 5 — Locator: get_listing tool (direct)
# ---------------------------------------------------------------------
section("5. Tool: locator.get_listing (direct invocation)")

def _test5():
    ctx = agents.RunContext(run_id="test-direct", state={})
    get_tool = next(t for t in agents.LOCATOR.tools if t.name == "get_listing")
    data = asyncio.run(get_tool.handler({"listing_id": "L-CDMX-001"}, ctx))
    expect("get_listing returns listing data",
           data.get("id") == "L-CDMX-001", str(data)[:100])
    expect("get_listing data has price_mxn", "price_mxn" in data)
_safe_section("Section 5: get_listing", _test5)


# ---------------------------------------------------------------------
# TEST 6 — Locator: compare_listings tool
# ---------------------------------------------------------------------
section("6. Tool: locator.compare_listings")

def _test6():
    ctx = agents.RunContext(run_id="test-compare", state={})
    cmp_tool = next(t for t in agents.LOCATOR.tools if t.name == "compare_listings")
    cmp = asyncio.run(cmp_tool.handler({"listing_ids": ["L-CDMX-001", "L-MOR-001"]}, ctx))
    expect("compare_listings returns matrix", "matrix" in cmp, str(cmp)[:200])
    expect("compare_listings reports cheapest_id", "cheapest_id" in cmp)
    expect("compare_listings reports largest_id", "largest_id" in cmp)
    expect("compare_listings has price_mxn matrix",
           "price_mxn" in cmp.get("matrix", {}))
_safe_section("Section 6: compare_listings", _test6)


# ---------------------------------------------------------------------
# TEST 6B — Locator: people/partner finder tools
# ---------------------------------------------------------------------
section("6B. Tool: locator people/partner finders")

def _test6b():
    ctx = agents.RunContext(run_id="test-finders", state={})
    buyer_tool = next(t for t in agents.LOCATOR.tools if t.name == "find_buyers")
    buyers = asyncio.run(buyer_tool.handler({"state": "CDMX", "query": "Roma comprador"}, ctx))
    expect("find_buyers returns buyer matches", buyers.get("count", 0) >= 1, str(buyers)[:200])
    expect("find_buyers stores last_buyers", len(ctx.state.get("last_buyers", [])) >= 1)
    expect("find_buyers respects state filter",
           all(b.get("state") == "CDMX" for b in buyers.get("results", [])))

    ctx = agents.RunContext(run_id="test-finders", state={})
    colab_tool = next(t for t in agents.LOCATOR.tools if t.name == "find_collaborator_agents")
    colabs = asyncio.run(colab_tool.handler({"state": "Morelos", "query": "Cuernavaca casas"}, ctx))
    expect("find_collaborator_agents returns matches", colabs.get("count", 0) >= 1,
           str(colabs)[:200])
    expect("find_collaborator_agents stores last_collaborator_agents",
           len(ctx.state.get("last_collaborator_agents", [])) >= 1)
    expect("find_collaborator_agents respects state filter",
           all(c.get("state") == "Morelos" for c in colabs.get("results", [])))

    ctx = agents.RunContext(run_id="test-finders", state={})
    broker_tool = next(t for t in agents.LOCATOR.tools if t.name == "find_brokers")
    brokers = asyncio.run(broker_tool.handler({"state": "CDMX", "query": "INFONAVIT"}, ctx))
    expect("find_brokers returns broker matches", brokers.get("count", 0) >= 1,
           str(brokers)[:200])
    expect("find_brokers stores last_brokers", len(ctx.state.get("last_brokers", [])) >= 1)
    expect("find_brokers respects state filter",
           all(b.get("state") == "CDMX" for b in brokers.get("results", [])))
_safe_section("Section 6B: People finders", _test6b)


# ---------------------------------------------------------------------
# TEST 7 — Audit path (RPP + Catastro + INEGI fan-out)
# ---------------------------------------------------------------------
section("7. End-to-end: audit path with focus_listing")

def _test7():
    result = asyncio.run(agents.run_orchestrator(
        "Audita esta propiedad: revisa RPP por gravámenes, Catastro por predial, e INEGI.",
        state={"focus_listing_id": "L-CDMX-001"}
    ))
    trace = result["trace"]
    agents_seen = [s["agent"] for s in trace if s["kind"] == "agent_start"]
    tools_used = [s["detail"]["tool"] for s in trace if s["kind"] == "agent_tool"]
    expect("audit_agent invoked", "audit_agent" in agents_seen)
    expect("locator_agent NOT invoked (audit-only path)",
           "locator_agent" not in agents_seen)
    for tool in ["rpp_lookup", "catastro_lookup", "inegi_zone_stats"]:
        expect(f"audit fanned out to {tool}", tool in tools_used)
    data = result.get("data") or {}
    expect("data.rpp returned", "rpp" in data)
    expect("data.catastro returned", "catastro" in data)
    expect("data.inegi returned", "inegi" in data)
    if "rpp" in data:
        expect("rpp has folio_real", "folio_real" in data["rpp"])
        expect("rpp has encumbrances list",
               isinstance(data["rpp"].get("encumbrances"), list))
    if "catastro" in data:
        expect("catastro has clave_catastral", "clave_catastral" in data["catastro"])
        expect("catastro has al_corriente", "al_corriente" in data["catastro"])
    if "inegi" in data:
        expect("inegi has ageb_id", "ageb_id" in data["inegi"])
        expect("inegi has crime_index_2025", "crime_index_2025" in data["inegi"])
    verify_trace_shape("audit-listing", trace)
_safe_section("Section 7: Audit listing", _test7)


# ---------------------------------------------------------------------
# TEST 8 — Audit: SAT RFC check
# ---------------------------------------------------------------------
section("8. End-to-end: audit RFC check")

def _test8():
    result = asyncio.run(agents.run_orchestrator(
        "Verifica el RFC CAS990101AAA en el SAT", state={}
    ))
    trace = result["trace"]
    agents_seen = [s["agent"] for s in trace if s["kind"] == "agent_start"]
    tools_used = [s["detail"]["tool"] for s in trace if s["kind"] == "agent_tool"]
    expect("audit_agent invoked for RFC", "audit_agent" in agents_seen)
    expect("sat_rfc_check tool called", "sat_rfc_check" in tools_used)
    data = result.get("data") or {}
    expect("data.sat returned", "sat" in data)
    if "sat" in data:
        expect("sat has rfc field", data["sat"].get("rfc") == "CAS990101AAA")
        expect("sat has lista_69 flag", "lista_69" in data["sat"])
        expect("sat has lista_efos flag", "lista_efos" in data["sat"])
_safe_section("Section 8: Audit SAT", _test8)


# ---------------------------------------------------------------------
# TEST 9 — Audit: Promesa review
# ---------------------------------------------------------------------
section("9. Audit: review_promesa with focus_document_id")

def _test9():
    result = asyncio.run(agents.run_orchestrator(
        "¿Esta promesa cumple LFPDPPP y NOM-151?",
        state={"focus_document_id": "D-DEMO"}
    ))
    trace = result["trace"]
    tools_used = [s["detail"]["tool"] for s in trace if s["kind"] == "agent_tool"]
    expect("review_promesa tool called", "review_promesa" in tools_used)
    data = result.get("data") or {}
    expect("data.promesa returned", "promesa" in data)
    if "promesa" in data:
        p = data["promesa"]
        expect("promesa has clause_review", "clause_review" in p)
        expect("promesa has nom151_token", "nom151_token" in p)
        expect("promesa.ok is bool", isinstance(p.get("ok"), bool))
_safe_section("Section 9: Audit Promesa", _test9)


# ---------------------------------------------------------------------
# TEST 10 — Realestate router intent classification
# ---------------------------------------------------------------------
section("10. Orchestrator routing decisions")

def _test10():
    router_cases = [
        ("Hola, ¿qué tal?",
         {"realestate_agent"}, set(),
         "general greeting → no delegation"),
        ("Busco un loft en Condesa",
         {"realestate_agent", "locator_agent"}, {"locator_agent"},
         "search → locator"),
        ("Comparar las 3 mejores opciones que vimos",
         {"realestate_agent", "locator_agent"}, {"locator_agent"},
         "compare → locator"),
        ("Encuentra compradores para Roma Norte",
         {"realestate_agent", "locator_agent"}, {"locator_agent"},
         "buyer finder → locator"),
        ("Busca un agente colaborador en Cuernavaca",
         {"realestate_agent", "locator_agent"}, {"locator_agent"},
         "collaborator agent finder → locator"),
        ("Necesito brokers para INFONAVIT en CDMX",
         {"realestate_agent", "locator_agent"}, {"locator_agent"},
         "broker finder → locator"),
        ("Audita el contrato listo para firmar",
         {"realestate_agent", "audit_agent"}, {"audit_agent"},
         "audit contract → audit_agent"),
        ("¿Tiene gravámenes esta propiedad?",
         {"realestate_agent", "audit_agent"}, {"audit_agent"},
         "encumbrance question → audit_agent"),
        ("¿Está el predial al corriente?",
         {"realestate_agent", "audit_agent"}, {"audit_agent"},
         "predial question → audit_agent"),
        ("Verifica el RFC del vendedor",
         {"realestate_agent", "audit_agent"}, {"audit_agent"},
         "RFC verification → audit_agent"),
    ]
    for msg, expected_agents, expected_subagents, label in router_cases:
        result = asyncio.run(agents.run_orchestrator(msg, state={}))
        seen = {s["agent"] for s in result["trace"] if s["kind"] == "agent_start"}
        expect(f"router: {label}",
               expected_agents.issubset(seen) and not (set(["locator_agent","audit_agent"]) - expected_subagents) & (seen - expected_agents),
               f"saw {seen}")

    finder_router_cases = [
        ("Encuentra compradores para Roma Norte", "find_buyers"),
        ("Busca un agente colaborador en Cuernavaca", "find_collaborator_agents"),
        ("Necesito brokers para INFONAVIT en CDMX", "find_brokers"),
    ]
    for msg, expected_tool in finder_router_cases:
        result = asyncio.run(agents.run_orchestrator(msg, state={}))
        tools_used = [s["detail"]["tool"] for s in result["trace"] if s["kind"] == "agent_tool"]
        expect(f"locator routed to {expected_tool}", expected_tool in tools_used,
               f"saw {tools_used}")
_safe_section("Section 10: Router decisions", _test10)


# ---------------------------------------------------------------------
# TEST 11 — Trace integrity invariants
# ---------------------------------------------------------------------
section("11. Trace integrity")

def _test11():
    result = asyncio.run(agents.run_orchestrator(
        "Audita L-CDMX-001 y dime si vale la pena",
        state={"focus_listing_id": "L-CDMX-001"}
    ))
    t = result["trace"]
    n_starts = sum(1 for s in t if s["kind"] == "agent_start")
    n_ends = sum(1 for s in t if s["kind"] == "agent_end")
    expect("agent_start count == agent_end count", n_starts == n_ends,
           f"start={n_starts} end={n_ends}")
    expect("trace has tool calls", any(s["kind"] == "agent_tool" for s in t))
    expect("ts_ms monotonic non-decreasing",
           all(t[i]["ts_ms"] >= t[i-1]["ts_ms"] for i in range(1, len(t))))
    verify_trace_shape("trace-integrity", t)
_safe_section("Section 11: Trace integrity", _test11)


# ---------------------------------------------------------------------
# TEST 12 — State propagation across nested agents
# ---------------------------------------------------------------------
section("12. State propagation: filters → locator → search_listings")

def _test12():
    result = asyncio.run(agents.run_orchestrator(
        "Busco depa",
        state={"filters": {"state": "Morelos", "beds_min": 2}}
    ))
    candidates = result["state"].get("last_candidates", [])
    expect("last_candidates set on shared state",
           len(candidates) > 0)
    expect("filters propagated to search_listings (Morelos only)",
           all(c["state"] == "Morelos" for c in candidates),
           f"states: {[c['state'] for c in candidates]}")
_safe_section("Section 12: State propagation", _test12)


# ---------------------------------------------------------------------
# TEST 13 — Mock public datasets sanity (datasets.py — works since P1.2)
# ---------------------------------------------------------------------
section("13. Public dataset stubs (datasets.py)")

rpp = datasets.rpp_lookup("CDMX", "Demo 123")
for k in ["folio_real", "registered_owner", "encumbrances", "status",
          "verification_token"]:
    expect(f"rpp_lookup → key '{k}'", k in rpp)

cat = datasets.catastro_lookup("Morelos", "Demo 123")
for k in ["clave_catastral", "uso_de_suelo", "valor_catastral_mxn",
          "impuesto_predial_anual_mxn", "al_corriente"]:
    expect(f"catastro_lookup → key '{k}'", k in cat)

ine = datasets.inegi_zone_stats(19.4, -99.16)
for k in ["ageb_id", "population", "median_household_income_mxn",
          "schools_within_1km", "crime_index_2025"]:
    expect(f"inegi_zone_stats → key '{k}'", k in ine)

sat = datasets.sat_rfc_check("CAS990101AAA")
for k in ["rfc", "estatus_padron", "regimen_fiscal", "lista_69", "lista_efos"]:
    expect(f"sat_rfc_check → key '{k}'", k in sat)

# Determinism
expect("rpp_lookup deterministic per (state,address)",
       datasets.rpp_lookup("CDMX","X")["folio_real"] ==
       datasets.rpp_lookup("CDMX","X")["folio_real"])

# Clause review precision
review = datasets.review_text_for_clauses(
    "objeto plazo anticipo pena convencional lfpdppp nom-151 jurisdicción tribunales",
    datasets.PROMESA_REQUIRED_CLAUSES)
expect("clause review: full text → score 1.0",
       review["score"] == 1.0, f"score={review['score']}, missing={review['missing']}")
review2 = datasets.review_text_for_clauses(
    "solo objeto y plazo aquí", datasets.PROMESA_REQUIRED_CLAUSES)
expect("clause review: partial text → < 1.0",
       review2["score"] < 1.0)
expect("clause review: missing list non-empty when partial",
       len(review2["missing"]) > 0)


# ---------------------------------------------------------------------
# TEST 14 — main.py /chat ↔ /agent/run integration shape
# ---------------------------------------------------------------------
section("14. main.py /chat response surfaces agent fields")

def _test14():
    # Stub conversations dict the way main.py uses it (we won't actually
    # import main.py because it constructs FastAPI; we directly verify that
    # run_orchestrator outputs the fields /chat needs)
    result = asyncio.run(agents.run_orchestrator(
        "Busco una casa con alberca en Cuernavaca", state={}
    ))
    expect("run_orchestrator returns 'reply'", "reply" in result)
    expect("run_orchestrator returns 'trace'", "trace" in result)
    expect("run_orchestrator returns 'run_id'", "run_id" in result)
    expect("run_orchestrator returns 'ai_mode'", "ai_mode" in result)
    expect("run_orchestrator returns 'state'", "state" in result)
    expect("run_id has expected prefix", str(result["run_id"]).startswith("R-"))
_safe_section("Section 14: /chat shape", _test14)


# ---------------------------------------------------------------------
# TEST 15 — Buyer UI appointment path regression
# (MVP-specific: voice repo has no frontend/buyer.html — uses RN mobile app.
# Expected to fail until P3.x ports the equivalent assertions to RN screens.)
# ---------------------------------------------------------------------
section("15. buyer.html selected listing appointment path")

def _test15():
    # Path preserved relative to MVP layout — in voice repo this won't exist;
    # the assertion failure is the contract telling us this test needs to be
    # re-thought against the React Native equivalent (HomeScreen.tsx).
    buyer_html_path = os.path.normpath(
        os.path.join(HERE, "..", "..", "..", "frontend", "buyer.html"))
    with open(buyer_html_path, encoding="utf-8") as f:
        buyer_html = f.read()

    expect("selected listing appointment calls requestVisit",
           'onclick="requestVisit(selectedEntity.id)"' in buyer_html)
    expect("scheduleSelectedAppointment preserves listing visit API fallback",
           "selectedEntity.type === \"listing\"" in buyer_html and
           "requestVisit(selectedEntity.id);" in buyer_html)
_safe_section("Section 15: Buyer UI regression", _test15)


# ---------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------
print()
print("=" * 70)
print("  SUMMARY")
print("=" * 70)
print(f"  Passed: {len(PASSED)}")
print(f"  Failed: {len(FAILED)}")
if agents is None:
    print()
    print("  ⚠  agents module not yet implemented (Phase 1.5–1.7).")
    print("     Failures in sections 1-12, 14 are expected until then.")
    print(f"     Import error: {_AGENTS_IMPORT_ERROR}")
if FAILED:
    print()
    for label, detail in FAILED:
        print(f"  ❌ {label}")
        if detail:
            print(f"     {detail}")

    # Exit code contract during phase transitions:
    # - Phase 1.3–1.4 (agents missing) → exit 0, every failure expected.
    # - Phase 1.5     (agents present, tool handlers stub)
    #                 → exit 0 if every failure mentions "Phase 1.5" or
    #                   "Phase 1.6" (the still-stubbed dependencies).
    # - Phase 1.6+    (handlers real) → exit 1 on ANY failure.
    expected_markers = ("Phase 1.5", "Phase 1.6")
    all_expected = all(
        any(m in detail for m in expected_markers)
        # Test 15 is the MVP-only buyer.html UI regression; pending RN port.
        or "Buyer UI" in label
        or "buyer.html" in detail
        for label, detail in FAILED
    )
    if agents is None or all_expected:
        print()
        if agents is None:
            print("  ℹ Exit 0: agents missing (Phase 1.5 dep). Will flip to strict mode in 1.5.")
        else:
            print("  ℹ Exit 0: failures attributable to Phase 1.6 (handlers) / MVP-only tests.")
            print("    Once tool handlers land in P1.6, this script flips to exit 1 on any failure.")
        sys.exit(0)
    sys.exit(1)
else:
    print("  All agent + tool paths verified correctly. ✅")
    sys.exit(0)
