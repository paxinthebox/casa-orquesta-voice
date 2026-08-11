"""Tests for role-aware orchestrator guardrails and welcome copy."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VG = os.path.normpath(os.path.join(ROOT, "..", "voice-gateway"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if VG not in sys.path:
    sys.path.insert(0, VG)

from agents.guardrails import idle_orchestrator_reply, role_proactivity  # noqa: E402
from pipeline.session import welcome_message  # noqa: E402


def test_seller_idle_reply_mentions_publicar():
    reply = idle_orchestrator_reply("seller")
    assert "publicar" in reply.lower()
    assert "ofertas" in reply.lower()


def test_buyer_idle_reply_mentions_buscar():
    reply = idle_orchestrator_reply("buyer")
    assert "propiedad" in reply.lower() or "buscar" in reply.lower()


def test_role_proactivity_seller():
    block = role_proactivity("seller")
    assert "client_role=seller" in block
    assert "compradores" in block.lower()


def test_welcome_message_seller():
    msg = welcome_message({"client_role": "seller"})
    assert "publicar" in msg.lower()


def test_welcome_message_buyer_default():
    msg = welcome_message({"client_role": "buyer"})
    assert "buscas" in msg.lower() or "casa" in msg.lower()
