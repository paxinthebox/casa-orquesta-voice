"""
OrchestratorStreamClient — Phase 2.3.

A thin async SSE consumer that talks to the orchestrator's
`POST /agent/run/stream` endpoint. Emits typed events:

    {"kind": "agent_start" | "agent_tool" | "tool_result" | "agent_end"
            | "text_delta" | "run_end" | "error",
     "agent": str,
     "ts_ms": int,
     "detail": dict,
     "run_id": str}

Design notes:
  * Async generator — `async for ev in client.stream(...)`.
  * Injectable HTTP client so unit tests don't need httpx installed.
  * Cancel-safe: closing the underlying response mid-stream is the
    contract barge-in relies on.

The orchestrator's wire format (matches services/orchestrator/main.py
`_sse()`):

    data: {"kind":"text_delta", ...}\n\n
    data: {"kind":"run_end", ...}\n\n

We tolerate empty keep-alive lines and lines that don't start with
`data:` (comment lines per the SSE spec).
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Callable, Optional, Protocol


# ----- Injectable HTTP surface (matches what httpx.AsyncClient.stream gives us) -----
class _StreamResponse(Protocol):
    status_code: int

    async def aiter_lines(self) -> AsyncIterator[str]: ...
    async def aclose(self) -> None: ...


class _StreamCtx(Protocol):
    """Object returned by `client.stream(...)`; works as async context manager."""

    async def __aenter__(self) -> _StreamResponse: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...


class _HTTPClient(Protocol):
    def stream(self, method: str, url: str, *, json: dict,
               headers: Optional[dict] = None,
               timeout: Optional[float] = None) -> _StreamCtx: ...

    async def aclose(self) -> None: ...


HTTPFactory = Callable[[], _HTTPClient]


def _default_http_factory() -> _HTTPClient:
    """Lazy import of httpx so tests without it can still load this module."""
    import httpx  # type: ignore

    # Long timeout: SSE streams can stay open for minutes during an agent run.
    return httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=120.0,
                                                   write=10.0, pool=10.0))


# ----- The client -----
class OrchestratorStreamClient:
    """Owns one HTTP client; can be reused across many `stream()` calls."""

    def __init__(
        self,
        base_url: str,
        *,
        tenant_id: str,
        user_id: str,
        http_factory: HTTPFactory = _default_http_factory,
    ):
        self.base_url = base_url.rstrip("/")
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._http: Optional[_HTTPClient] = None
        self._http_factory = http_factory

    def _get_http(self) -> _HTTPClient:
        if self._http is None:
            self._http = self._http_factory()
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None

    async def stream(
        self,
        message: str,
        state: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """
        POST to /agent/run/stream and yield each parsed event.

        Caller is responsible for breaking out of the loop (e.g. for
        barge-in). When the caller exits the loop, the underlying
        response is closed by the context manager.
        """
        body = {
            "message": message,
            "state": state or {},
            "tenant_id": self.tenant_id,
        }
        if state and state.get("conversation_id"):
            body["conversation_id"] = state["conversation_id"]
        headers = {
            "Accept": "text/event-stream",
            "X-Tenant-Id": self.tenant_id,
            "X-User-Id": self.user_id,
        }
        url = f"{self.base_url}/agent/run/stream"
        http = self._get_http()
        async with http.stream("POST", url, json=body, headers=headers,
                               timeout=120.0) as resp:
            if getattr(resp, "status_code", 0) >= 400:
                yield {
                    "kind": "error",
                    "agent": "orchestrator",
                    "ts_ms": 0,
                    "detail": {"message": f"HTTP {resp.status_code}"},
                    "run_id": "",
                }
                return
            async for line in resp.aiter_lines():
                if not line:
                    continue
                # SSE spec: comments start with ':'; we ignore them.
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[5:].lstrip()
                if not payload:
                    continue
                try:
                    ev = json.loads(payload)
                except (ValueError, TypeError):
                    continue
                yield ev
                # `run_end` is the natural terminator. `error` is too.
                kind = ev.get("kind")
                if kind in ("run_end", "error"):
                    return
