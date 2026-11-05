"""
Export prices: reading Eurostat and FAO, and the export view's rules - what is
compared, what is only listed, and how a state's cost to the port or border
is built.

    pytest tests/test_export_prices.py -v
"""

import json, os, sys, importlib.util

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config.settings import (COMMODITIES, EU_PRODUCTS, WA_PRODUCTS, EXPORT_PROHIBITED, STATE_MARKETS,
                             EU_MIN_TONNES, FREIGHT_NGN_PER_KG_PER_100KM, CFA_PER_EUR)


def load(step):
    spec = importlib.util.spec_from_file_location(step, os.path.join(ROOT, "pipeline", f"{step}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fetch = load("fetch_export_prices")
view = load("20_export_prices")

# Rows from Eurostat's reply of 16 Oct 2026 (EU imports from Nigeria, Aug 2026)
EUROSTAT = """DATAFLOW,LAST UPDATE,freq,reporter,partner,product,flow,indicators,TIME_PERIOD,OBS_VALUE
ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU,NG,12074090,1,QUANTITY_IN_100KG,2026-08,16285.34
ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU,NG,12074090,1,VALUE_IN_EUROS,2026-08,2012411
ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU,NG,12019000,1,QUANTITY_IN_100KG,2026-07,0.42
ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU,NG,12019000,1,VALUE_IN_EUROS,2026-07,301
ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU,NG,07133500,1,QUANTITY_IN_100KG,2026-07,0.01
ESTAT:DS-045409(1.0),16/10/26 11:00:00,M,EU,NG,07133500,1,VALUE_IN_EUROS,2026-07,1
"""


def test_config_names_only_our_commodities_and_zone_states():
    for c in list(EU_PRODUCTS) + list(WA_PRODUCTS.values()) + list(EXPORT_PROHIBITED):
        assert c in COMMODITIES, c
    with open(os.path.join(ROOT, "data", "external", "zones_config.json")) as f:
        zone_states = {s for z in json.load(f).values() for s in z["states"]}
    assert set(STATE_MARKETS) == zone_states


def test_eu_price_is_declared_value_over_weight_for_our_products_only():
    eu = fetch.eu_prices(EUROSTAT)
    assert set(eu["commodity"]) == {"Sesame", "Soybeans"}          # cowpeas (07133500) is not an EU product
    sesame = eu[eu["commodity"] == "Sesame"].iloc[0]
    assert sesame["quantity_t"] == pytest.approx(1628.53, abs=0.01)
    assert sesame["price_local_t"] == pytest.approx(2012411 / 1628.534, abs=0.01)   # EUR 1,235.72/t
    assert sesame["period"] == "2026-08-01" and sesame["currency"] == "EUR"


def test_fao_unit_labels():
    assert [fetch.unit_kg(u) for u in ["100 kg", "Kg", "25 kg", "120 kg", "Coro", "1 unit"]] == \
           [100, 1, 25, 120, None, None]


def test_cfa_converts_to_naira_through_the_fixed_euro_peg():
    df = pd.DataFrame([{"period": "2026-10-01", "currency": "XOF", "price_local_t": 160000.0},
                       {"period": "2026-10-01", "currency": "EUR", "price_local_t": 1000.0}])
    eur = pd.DataFrame({"date": ["2026-10-02", "2026-10-20"], "ngn_per_eur": [1500.0, 1520.0]})
    out = fetch.to_naira(df, eur)
    assert out["price_ngn_t"].tolist() == [round(160000 * 1510 / CFA_PER_EUR), 1510000]


def test_road_distances_are_close_to_known_road_distances():
    assert 950 <= view.road_km(STATE_MARKETS["Kano"][1:], (6.44, 3.37)) <= 1200       # Kano - Lagos ports
    assert 220 <= view.road_km(STATE_MARKETS["Borno"][1:], (12.08, 15.03)) <= 300     # Maiduguri - Kousseri
    assert view.exit_for("Rivers", "EU", None)[0] == "Onne port"
    assert view.exit_for("Lagos", "EU", None)[0] == "Lagos ports (Apapa)"


def row(commodity, region, market, period, price, currency, price_type, qty=None, product="p", country="c"):
    return {"period": period, "region": region, "country": country, "market": market, "commodity": commodity,
            "product": product, "product_code": "", "price_type": price_type, "price_local_t": price,
            "currency": currency, "quantity_t": qty, "price_ngn_t": None, "source_name": "test"}


EU_ROW = dict(region="EU", market="EU border (all member states)", currency="EUR",
              price_type="import value (CIF, EU border)")
EUR = pd.DataFrame({"date": ["2026-11-04"], "ngn_per_eur": [1500.0]})
ZONAL = {"national_anchors": {"Sesame": {"price": 1_500_000}, "Sorghum": {"price": 340_000},
                              "Maize (white)": {"price": 430_000}, "Cashew Nuts": {"price": 1_800_000}},
         "zones": {"North West": {"states": {
             "Kano": {"Sesame": {"state_price": 1_250_000, "price_factor": 0.83, "factor_source": "estimated"},
                      "Sorghum": {"state_price": 320_000, "price_factor": 0.94, "factor_source": "measured_fews"}}}},
                   "South West": {"states": {
             "Lagos": {"Sesame": {"state_price": 1_800_000, "price_factor": 1.2, "factor_source": "estimated"},
                       "Sorghum": {"state_price": 390_000, "price_factor": 1.14, "factor_source": "measured_fews"}}}}}}


def build(rows, quotes=None):
    return view.build(pd.DataFrame(rows), EUR, ZONAL, quotes or {}, "2026-11-05")["commodities"]


def test_state_cost_is_buy_price_plus_freight_and_room_is_what_is_left():
    rep = build([row("Sesame", period="2026-08-01", price=1200.0, qty=1600, **EU_ROW)])
    d = rep["Sesame"]["destinations"][0]
    assert d["compared"] and d["price_ngn_t"] == 1_800_000                 # EUR 1,200 x N1,500
    assert d["gap_vs_national_pct"] == 20.0
    kano = next(r for r in d["by_state"] if r["state"] == "Kano")
    assert kano["freight_ngn_t"] == round(kano["distance_km"] * FREIGHT_NGN_PER_KG_PER_100KM * 10)
    assert kano["cost_at_exit_ngn_t"] == 1_250_000 + kano["freight_ngn_t"]
    assert kano["room_ngn_t"] == 1_800_000 - kano["cost_at_exit_ngn_t"]
    assert [r["state"] for r in d["by_state"]] == ["Kano", "Lagos"]           # cheapest at the port first
    assert d["cheapest_state"]["state"] == "Kano" and "estimates" in d["cheapest_state"]["note"]


def test_eu_month_below_the_tonnage_floor_is_not_the_price():
    rep = build([row("Sesame", period="2026-07-01", price=1100.0, qty=EU_MIN_TONNES * 50, **EU_ROW),
                 row("Sesame", period="2026-08-01", price=7000.0, qty=EU_MIN_TONNES / 10, **EU_ROW)])
    assert rep["Sesame"]["destinations"][0]["period"] == "2026-07"


def test_prohibited_exports_are_listed_never_costed():
    rep = build([row("Maize (white)", "West Africa", "Maradi", "2026-10-01", 170000.0, "XOF", "wholesale")])
    e = rep["Maize (white)"]
    assert e["export_allowed"] is False and "Export (Prohibition) Act" in e["export_note"]
    d = e["destinations"][0]
    assert not d["compared"] and d["by_state"] == [] and d["gap_vs_national_pct"] is None


def test_cashew_kernels_retail_and_old_prices_are_listed_not_compared():
    rep = build([row("Cashew Nuts", period="2026-08-01", price=5700.0, qty=276, **EU_ROW),
                 row("Sorghum", "West Africa", "Cotonou", "2026-08-01", 485000.0, "XOF", "retail"),
                 row("Sorghum", "West Africa", "Zinder", "2026-01-01", 200000.0, "XOF", "wholesale")])
    assert "kernels" in rep["Cashew Nuts"]["destinations"][0]["note"]
    for d in rep["Cashew Nuts"]["destinations"] + rep["Sorghum"]["destinations"]:
        assert not d["compared"] and d["by_state"] == []


def test_a_market_uses_its_wholesale_price_before_retail():
    rep = build([row("Sorghum", "West Africa", "Maradi", "2026-10-01", 160000.0, "XOF", "wholesale", product="Sorghum"),
                 row("Sorghum", "West Africa", "Maradi", "2026-10-01", 250000.0, "XOF", "retail",
                     product="Sorghum (white)")])
    (d,) = rep["Sorghum"]["destinations"]
    assert d["price_type"] == "wholesale" and d["compared"]
    assert d["price_ngn_t"] == round(160000 * 1500 / CFA_PER_EUR)


def test_a_recent_field_quote_replaces_the_estimated_state_price(tmp_path):
    p = tmp_path / "field.csv"
    pd.DataFrame([
        {"date": "2026-11-01", "commodity": "Sesame", "market": "Dawanau", "state": "Kano", "price_ngn": 60000,
         "unit": "50kg bag", "price_level": "wholesale", "reported_by": "test", "notes": ""},
        {"date": "2026-08-01", "commodity": "Sesame", "market": "Mile 12", "state": "Lagos", "price_ngn": 1,
         "unit": "kg", "price_level": "wholesale", "reported_by": "too old", "notes": ""},
    ]).to_csv(p, index=False)
    quotes = view.field_state_prices(str(p), today="2026-11-05")
    assert set(quotes) == {("Sesame", "Kano")}
    rep = build([row("Sesame", period="2026-08-01", price=1200.0, qty=1600, **EU_ROW)], quotes)
    kano = next(r for r in rep["Sesame"]["destinations"][0]["by_state"] if r["state"] == "Kano")
    assert kano["buy_price_ngn_t"] == 1_200_000 and kano["buy_price_basis"] == "field quote"


def test_api_lists_prices_abroad_and_one_commodity_by_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api
    rows = [row("Sesame", period="2026-08-01", price=1200.0, qty=1600, **EU_ROW),
            row("Sorghum", "West Africa", "Maradi", "2026-10-01", 160000.0, "XOF", "wholesale")]
    rep = view.build(pd.DataFrame(rows), EUR, ZONAL, {}, "2026-11-05")
    (tmp_path / "export_prices_2026-11-05.json").write_text(json.dumps(rep, allow_nan=False), encoding="utf-8")
    monkeypatch.setattr(api, "INTEL_DIR", str(tmp_path))
    client = TestClient(api.app)

    allp = client.get("/export-prices").json()
    assert set(allp["commodities"]) == {"Sesame", "Sorghum"} and allp["fx"]["ngn_per_eur"] == 1500.0
    assert "by_state" not in allp["commodities"]["Sesame"]["destinations"][0]
    assert set(client.get("/export-prices", params={"region": "EU"}).json()["commodities"]) == {"Sesame"}

    one = client.get("/export-prices/sesame", params={"state": "kano"}).json()
    assert one["commodity"] == "Sesame"
    assert [r["state"] for r in one["destinations"][0]["by_state"]] == ["Kano"]
    assert client.get("/export-prices/ginger").status_code == 404
