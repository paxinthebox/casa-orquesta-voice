"""
Eval assertion helpers — shared by run_evals.py and promptfoo python assertions.
"""
from __future__ import annotations

from typing import Any


def agents_from_trace(trace: list[dict]) -> set[str]:
    return {s["agent"] for s in trace if s.get("kind") == "agent_start"}


def subagents_from_trace(trace: list[dict]) -> set[str]:
    invoked: set[str] = set()
    for step in trace:
        if step.get("kind") != "agent_tool":
            continue
        tool = (step.get("detail") or {}).get("tool")
        if tool in ("locator_agent", "audit_agent"):
            invoked.add(tool)
    return invoked


def tools_from_trace(trace: list[dict]) -> list[str]:
    out: list[str] = []
    for step in trace:
        if step.get("kind") != "agent_tool":
            continue
        tool = (step.get("detail") or {}).get("tool")
        if tool:
            out.append(str(tool))
    return out


def prompt_guard_blocked(trace: list[dict]) -> bool:
    for step in trace:
        if step.get("kind") != "policy":
            continue
        detail = step.get("detail") or {}
        if detail.get("policy") == "prompt_guard" and detail.get("allow") is False:
            return True
    return False


def check_assertions(result: dict, assert_spec: dict) -> list[str]:
    """Return list of failure messages (empty = pass)."""
    failures: list[str] = []
    trace = result.get("trace") or []
    reply = str(result.get("reply") or "")
    reply_lower = reply.lower()

    if "agents_invoked" in assert_spec:
        seen = agents_from_trace(trace)
        spec = assert_spec["agents_invoked"]
        for agent in spec.get("includes") or []:
            if agent not in seen:
                failures.append(f"agents_invoked missing {agent!r} (saw {sorted(seen)})")
        for agent in spec.get("excludes") or []:
            if agent in seen:
                failures.append(f"agents_invoked should not include {agent!r}")

    if "subagents" in assert_spec:
        subs = subagents_from_trace(trace)
        spec = assert_spec["subagents"]
        for agent in spec.get("includes") or []:
            if agent not in subs:
                failures.append(f"subagents missing {agent!r} (saw {sorted(subs)})")
        for agent in spec.get("excludes") or []:
            if agent in subs:
                failures.append(f"subagents should not include {agent!r}")

    if "tools_used" in assert_spec:
        tools = tools_from_trace(trace)
        spec = assert_spec["tools_used"]
        for tool in spec.get("includes") or []:
            if tool not in tools:
                failures.append(f"tools_used missing {tool!r} (saw {tools})")
        includes_any = spec.get("includes_any") or []
        if includes_any and not any(t in tools for t in includes_any):
            failures.append(f"tools_used missing any of {includes_any} (saw {tools})")

    if "prompt_guard" in assert_spec:
        blocked = prompt_guard_blocked(trace)
        want_allow = assert_spec["prompt_guard"].get("allow")
        if want_allow is True and blocked:
            failures.append("prompt_guard should allow but blocked")
        if want_allow is False and not blocked:
            failures.append("prompt_guard should block but allowed")

    if "reply" in assert_spec:
        spec = assert_spec["reply"]
        if "min_chars" in spec and len(reply) < spec["min_chars"]:
            failures.append(f"reply too short ({len(reply)} < {spec['min_chars']})")
        if "max_chars" in spec and len(reply) > spec["max_chars"]:
            failures.append(f"reply too long ({len(reply)} > {spec['max_chars']})")
        contains_any = spec.get("contains_any") or []
        if contains_any and not any(tok.lower() in reply_lower for tok in contains_any):
            failures.append(f"reply missing any of {contains_any!r}")
        for tok in spec.get("not_contains") or []:
            if tok.lower() in reply_lower:
                failures.append(f"reply must not contain {tok!r}")

    if "trace" in assert_spec:
        spec = assert_spec["trace"]
        if spec.get("balanced_agent_lifecycle"):
            starts = sum(1 for s in trace if s.get("kind") == "agent_start")
            ends = sum(1 for s in trace if s.get("kind") == "agent_end")
            if starts != ends:
                failures.append(f"unbalanced trace lifecycle start={starts} end={ends}")

    if "state" in assert_spec:
        state = result.get("state") or {}
        for path, expected in (assert_spec["state"] or {}).items():
            actual = _get_path(state, path)
            if actual != expected:
                failures.append(f"state.{path} expected {expected!r}, got {actual!r}")

    return failures


def _get_path(obj: dict, path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur
