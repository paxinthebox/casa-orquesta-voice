"""
Resolve search filters for a turn — single entry point for voice + chat.

Claude tool args must not override this; matching always consumes the result.
"""
from __future__ import annotations

from client_profile import merge_profile_and_message_filters
from query_parse import extract_filters


def resolve_search_filters(state: dict, message: str) -> dict:
    """Parse message + profile + session → normalized filter dict for matching."""
    parsed = extract_filters(message)
    return merge_profile_and_message_filters(state, message, parsed)
