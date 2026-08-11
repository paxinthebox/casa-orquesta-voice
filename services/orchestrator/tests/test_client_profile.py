"""Tests for per-thread client profile → search filter baseline."""
from client_profile import client_profile_to_filters, merge_profile_and_message_filters


def test_profile_maps_listing_mode_rent():
    f = client_profile_to_filters({
        "listing_mode": "rent",
        "budget_mxn": 40_000,
        "area": "Roma Norte",
        "state": "CDMX",
        "property_types": ["departamento"],
    })
    assert f["listing_mode"] == "rent"
    assert f["price_max_mxn"] == 40_000
    assert f["state"] == "CDMX"


def test_profile_maps_budget_zone_and_type():
    f = client_profile_to_filters({
        "client_name": "María",
        "budget_mxn": 8_000_000,
        "state": "CDMX",
        "area": "Condesa",
        "property_type": "departamento",
        "beds_min": 2,
        "baths_min": 2,
        "loan_type": "INFONAVIT",
        "features": ["terraza", "parking", "elevador"],
    })
    assert f["price_max_mxn"] == 8_000_000
    assert f["state"] == "CDMX"
    assert f["city"] == "Ciudad de México"
    assert f["neighborhood"].lower() == "condesa"
    assert f["type"] == "departamento"
    assert f["beds_min"] == 2
    assert f["baths_min"] == 2
    assert "mortgage" not in f
    assert "terraza" in f["features"]
    assert "estacionamiento" in f["features"]
    assert "elevador" in f["features"]


def test_profile_submit_unions_features_instead_of_overwriting():
    state = {
        "client_profile": {
            "budget_mxn": 8_000_000,
            "features": ["elevador", "seguridad", "parking"],
            "property_types": ["departamento"],
        },
        "filters": {},
    }
    parsed = {"features": ["terraza", "estacionamiento"], "price_max_mxn": 7_600_000}
    merged = merge_profile_and_message_filters(
        state,
        "Busco propiedades para un cliente llamado Ana que busca departamento "
        "hasta 8,000,000 pesos con características: elevador, seguridad, parking "
        "Análisis de presupuesto: valor de propiedad $7,600,000 MXN.",
        parsed,
    )
    feats = {f.lower() for f in (merged.get("features") or [])}
    assert "elevador" in feats
    assert "seguridad" in feats
    assert "estacionamiento" in feats
    assert "terraza" in feats
    assert merged["price_max_mxn"] == 8_000_000


def test_profile_maps_multiple_property_types():
    f = client_profile_to_filters({
        "property_types": ["departamento", "casa"],
        "state": "CDMX",
    })
    assert f["types"] == ["departamento", "casa"]
    assert "type" not in f


def test_message_overrides_profile_baseline():
    state = {
        "client_profile": {
            "budget_mxn": 8_000_000,
            "area": "Condesa",
            "property_type": "departamento",
        },
        "filters": {},
    }
    parsed = {"price_max_mxn": 6_000_000, "type": "casa"}
    merged = merge_profile_and_message_filters(
        state,
        "Busco casa hasta 6 millones",
        parsed,
    )
    assert merged["price_max_mxn"] == 6_000_000
    assert merged["type"] == "casa"
    assert merged["city"] == "Ciudad de México"


def test_profile_submit_keeps_formulary_budget_and_types():
    state = {
        "client_profile": {
            "budget_mxn": 8_000_000,
            "area": "Condesa",
            "state": "CDMX",
            "property_types": ["departamento", "casa"],
            "beds_min": 3,
        },
        "filters": {},
    }
    parsed = {
        "price_max_mxn": 7_600_000,
        "type": "departamento",
        "beds_min": 3,
    }
    merged = merge_profile_and_message_filters(
        state,
        "Busco propiedades para un cliente llamado Ana que busca departamento o casa "
        "en Condesa, CDMX con 3 recámaras hasta 8,000,000 pesos "
        "Análisis de presupuesto: valor de propiedad $7,600,000 MXN.",
        parsed,
    )
    assert merged["price_max_mxn"] == 8_000_000
    assert merged["types"] == ["departamento", "casa"]
    assert merged["city"] == "Ciudad de México"
    assert merged["neighborhood"].lower() == "condesa"
    assert "type" not in merged
    assert "mortgage" not in merged


def test_profile_submit_strips_credit_mortgage_from_prompt_parse():
    state = {
        "client_profile": {
            "budget_mxn": 8_000_000,
            "area": "Condesa",
            "state": "CDMX",
            "property_types": ["departamento"],
            "loan_type": "bancario",
        },
        "filters": {},
    }
    merged = merge_profile_and_message_filters(
        state,
        "Busco propiedades para un cliente que busca departamento en Condesa, CDMX "
        "hasta 8,000,000 pesos con pago o crédito bancario.",
        {"type": "departamento", "mortgage": "bancario", "price_max_mxn": 8_000_000},
    )
    assert "mortgage" not in merged
    assert merged["price_max_mxn"] == 8_000_000


def test_profile_submit_keeps_formulary_zone_over_prompt_parse():
    state = {
        "client_profile": {
            "budget_mxn": 5_000_000,
            "area": "Xochitepec",
            "state": "Morelos",
            "property_types": ["departamento"],
        },
        "filters": {},
    }
    parsed = {"state": "CDMX", "city": "Ciudad de México", "type": "casa"}
    merged = merge_profile_and_message_filters(
        state,
        "Busco propiedades para un cliente en CDMX con casa.",
        parsed,
    )
    assert merged["state"] == "Morelos"
    assert merged["city"] == "Xochitepec"
    assert merged["type"] == "departamento"


def test_stale_session_filters_do_not_override_rent_profile():
    state = {
        "client_profile": {
            "listing_mode": "rent",
            "budget_mxn": 35_000,
            "state": "CDMX",
            "area": "Roma Norte",
            "property_types": ["departamento"],
        },
        "filters": {
            "listing_mode": "sale",
            "state": "CDMX",
            "neighborhood": "condesa",
            "type": "casa",
        },
    }
    merged = merge_profile_and_message_filters(
        state,
        "Busco propiedades para un cliente en renta anual en Roma Norte hasta 35000 al mes",
        {"listing_mode": "sale", "type": "casa"},
    )
    assert merged["listing_mode"] == "rent"
    assert merged["neighborhood"] == "roma norte" or merged.get("neighborhood") == "Roma Norte"
    assert merged["type"] == "departamento"


def test_both_types_when_same_message_names_casa_and_departamento():
    merged = merge_profile_and_message_filters(
        {"client_profile": {}, "filters": {"type": "departamento"}},
        "casa o departamento en renta en Cuernavaca",
        {"types": ["departamento", "casa"], "listing_mode": "rent"},
    )
    assert merged.get("types") == ["departamento", "casa"]
    assert "type" not in merged


def test_voice_departamento_clears_stale_session_types():
    """Prior 'casa o departamento' must not widen when user asks only departamento."""
    merged = merge_profile_and_message_filters(
        {
            "client_profile": {},
            "filters": {
                "types": ["departamento", "casa"],
                "state": "Morelos",
                "city": "Cuernavaca",
            },
        },
        "Busco departamento en renta en Cuernavaca hasta 16 mil",
        {
            "type": "departamento",
            "listing_mode": "rent",
            "state": "Morelos",
            "city": "Cuernavaca",
            "price_max_mxn": 16_000,
        },
    )
    assert merged.get("type") == "departamento"
    assert "types" not in merged
    assert "property_types" not in merged


def test_voice_departamento_overrides_formulary_both_types():
    merged = merge_profile_and_message_filters(
        {
            "client_profile": {
                "property_types": ["departamento", "casa"],
                "listing_mode": "rent",
            },
            "filters": {},
        },
        "departamento en renta en Cuernavaca",
        {"type": "departamento", "listing_mode": "rent", "city": "Cuernavaca"},
    )
    assert merged.get("type") == "departamento"
    assert "types" not in merged

    state = {
        "client_profile": {
            "listing_mode": "rent",
            "budget_mxn": 35_000,
            "state": "CDMX",
            "area": "Roma Norte",
            "property_types": ["departamento"],
        },
        "filters": {},
    }
    parsed = {"listing_mode": "sale", "type": "casa"}
    merged = merge_profile_and_message_filters(
        state,
        "Quiero ver casas en venta",
        parsed,
    )
    assert merged["listing_mode"] == "rent"
    assert merged["type"] == "casa"


def test_voice_cuernavaca_clears_dual_state_pilot():
    merged = merge_profile_and_message_filters(
        {
            "client_profile": {"state": "both", "listing_mode": "rent"},
            "filters": {"states": ["CDMX", "Morelos"]},
        },
        "Casa en renta en Cuernavaca hasta 16000",
        {
            "listing_mode": "rent",
            "state": "Morelos",
            "city": "Cuernavaca",
            "price_max_mxn": 16000,
        },
    )
    assert "states" not in merged
    assert merged["state"] == "Morelos"
    assert merged["city"] == "Cuernavaca"


def test_profile_submit_routes_to_search_listings_not_credit_broker():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from agents import run_orchestrator

    prompt = (
        "Busco propiedades para un cliente llamado Test que busca departamento "
        "en Condesa, CDMX hasta 8,000,000 pesos Seguimiento crediticio bajo "
        "convenio del consumidor: broker de crédito: Juan."
    )
    state = {
        "client_profile": {
            "budget_mxn": 8_000_000,
            "state": "CDMX",
            "area": "Condesa",
            "property_types": ["departamento"],
        },
        "filters": {},
        "client_role": "buyer",
    }
    fake_search = AsyncMock(return_value={"count": 0, "results": []})
    with patch("agents.locator._h_search_listings", fake_search):
        result = asyncio.run(run_orchestrator(prompt, state))
    locator_tools = [
        s["detail"]["tool"]
        for s in result["trace"]
        if s["kind"] == "agent_tool" and s["agent"] == "locator_agent"
    ]
    assert "search_listings" in locator_tools
    assert "find_brokers" not in locator_tools
    fake_search.assert_awaited_once()
