"""People vs listing reply synthesis and routing regressions."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import agents  # noqa: E402
from reply_synth import format_supervisor_search_reply  # noqa: E402


def test_supervisor_people_reply_not_listings():
    data = {
        "count": 1,
        "results": [
            {
                "id": "CA-002",
                "name": "Fernando Soto",
                "state": "Morelos",
                "city": "Cuernavaca",
                "rating": 4.8,
            }
        ],
    }
    reply = format_supervisor_search_reply(
        filters={"city": "Cuernavaca", "state": "Morelos"},
        sub_output="Encontré 1 agentes colaboradores",
        data=data,
    )
    assert "millones" not in reply.lower()
    assert "Fernando Soto" in reply
    assert "colaborador" in reply.lower()


def test_people_search_after_listing_search_not_overwritten():
    state = {
        "filters": {"city": "Cuernavaca", "state": "Morelos"},
        "last_candidates": [
            {
                "id": "L-OLD",
                "title": "Casa vieja",
                "price_mxn": 5_000_000,
                "city": "Cuernavaca",
            }
        ],
    }
    result = asyncio.run(
        agents.run_orchestrator("Busca un agente colaborador en Cuernavaca", state=state)
    )
    reply = result["reply"]
    assert "millones" not in reply.lower()
    assert "colaborador" in reply.lower() or "Fernando" in reply


def test_collaborator_follow_up_message_with_focus():
    prompt = (
        'Envía este mensaje para agente colaborador CA-002 (Fernando Soto): '
        '"Hola Fernando, me gustaría coordinar una colaboración."'
    )
    result = asyncio.run(
        agents.run_orchestrator(
            prompt,
            state={
                "focus_person_id": "CA-002",
                "focus_person_name": "Fernando Soto",
                "focus_person_kind": "collaborator",
            },
        )
    )
    reply = result["reply"].lower()
    assert "fernando" in reply or "mensaje" in reply
    assert "millones" not in reply


def test_broker_schedule_follow_up():
    result = asyncio.run(
        agents.run_orchestrator(
            "Programa una cita para broker BR-001 en este horario disponible: Mañana 10:00. "
            "Sincroniza calendarios y confirma disponibilidad.",
            state={
                "focus_person_id": "BR-001",
                "focus_person_name": "Gerardo Hernández",
                "focus_person_kind": "broker",
            },
        )
    )
    assert "cita" in result["reply"].lower() or "horario" in result["reply"].lower()


def test_agente_inmobiliario_routes_to_collaborators():
    result = asyncio.run(
        agents.run_orchestrator("Necesito un agente inmobiliario en Cuernavaca", state={})
    )
    tools = [s["detail"]["tool"] for s in result["trace"] if s["kind"] == "agent_tool"]
    assert "find_collaborator_agents" in tools

