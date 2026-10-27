"""
Farm crop prices: reading ColdHubs and Business A.M., and choosing a farm's price.

    pytest tests/test_farm_prices.py -v
"""

import os, sys, importlib.util
from datetime import date

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load(step):
    spec = importlib.util.spec_from_file_location(step, os.path.join(ROOT, "pipeline", f"{step}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Sentences from Business A.M., 10 Oct 2026 ("Onion prices soar 27% in four days ...")
BAM_ARTICLE = "".join(f"<p>{s}</p>" for s in [
    "Prices of selected vegetables and staple produce rose sharply between October 5 and October 9, 2026.",
    "Market price data for the four-day period show that a 70-kilogramme bag of dry-season onions rose from "
    "N105,000 to N130,000, an increase of N25,000 or 23.8 per cent.",
    "Wet-season onions recorded an even larger percentage increase, climbing from N75,000 to N95,000 per 70kg bag, "
    "up N20,000 or 26.7 per cent.",
    "Ordinary tomatoes rose by N5,000, from N40,000 per 50kg basket to N45,000, representing a 12.5 per cent increase.",
    "Carrots rose from N50,000 to N55,000 per 60kg bag, a 10 per cent increase, while sweet potatoes climbed from "
    "N45,000 to N50,000 per 100kg bag, up 11.1 per cent.",
    "By October 9, dry-season onions cost N130,000 per 70kg bag, compared with N95,000 for wet-season onions of the "
    "same weight.",
    "Tatashe increased to N40,000 per 25kg bag, equivalent to N1,600 per kilogram, from N35,000 previously.",
    "Atarugu, also known as rodo, remained at N35,000 per 20kg bag, while shombo, or bawa pepper, held steady at "
    "N30,000 per 20kg bag.",
    "Hybrid tomatoes, however, remained at N35,000.",
])


def test_business_am_reads_the_newest_bag_price_of_each_item():
    f = load("fetch_produce_prices")
    rows = {r["item"]: r for r in f.parse_bam(BAM_ARTICLE, "2026-10-10", "https://example/a")}
    got = {i: (r["price_low_ngn"], r["package_kg"], r["package"]) for i, r in rows.items()}
    assert got == {
        "dry-season onions": (130000, 70.0, "70 kg bag"),
        "wet-season onions": (95000, 70.0, "70 kg bag"),
        "ordinary tomatoes": (45000, 50.0, "50 kg basket"),
        "sweet potatoes":    (50000, 100.0, "100 kg bag"),
        "tatashe":           (40000, 25.0, "25 kg bag"),     # not the N1,600 per kg figure
        "atarugu (rodo)":    (35000, 20.0, "20 kg bag"),
        "shombo (bawa)":     (30000, 20.0, "20 kg bag"),
    }, "hybrid tomatoes have no bag size in the article, so they are left out"
    assert {r["price_date"] for r in rows.values()} == {"2026-10-09"}
    assert rows["sweet potatoes"]["crop"] == "Sweet potato" and rows["tatashe"]["level"] == "wholesale"


def test_business_am_leaves_out_a_clause_naming_two_items_or_conflicting_prices():
    f = load("fetch_produce_prices")
    text = ("<p>Tatashe and shombo held at N35,000 per 25kg bag.</p>"
            "<p>Wet-season onions rose from N60,000 to N70,000 per 70kg bag.</p>"
            "<p>Wet-season onions also climbed from N65,000 to N75,000 per 70kg bag.</p>")
    assert f.parse_bam(text, "2026-09-11", "u") == []


def test_business_am_price_date_is_the_newest_date_given_before_the_post():
    f = load("fetch_produce_prices")
    assert f.bam_price_date("in the week to September 11, prices ...", "2026-09-11") == "2026-09-11"
    assert f.bam_price_date("comparing Monday, August 17, with Saturday, August 22", "2026-08-23") == "2026-08-22"
    assert f.bam_price_date("no dates here", "2026-10-03") == "2026-10-03"


COLDHUBS_REPORT = """
<p>THE WEEK AT A GLANCE Tomatoes (50kg) Price: N30,000 - 35,000 Pepper (25kg) Price: N20,000 - 25,000</p>
<table>
<tr><th>S/N</th><th>Produce</th><th>N/Central</th><th>N/East</th><th>N/West</th><th>S/East</th><th>S/South</th><th>S/West</th></tr>
<tr><td>1</td><td>Tomatoes</td><td>₦30,000-35,000</td><td>–</td><td>₦25,000-50,000</td><td>₦50,000-60,000</td><td>₦55,000-65,000</td><td>₦55,000-60,000</td></tr>
<tr><td>2</td><td>Pepper</td><td>₦20,000-25,000</td><td>–</td><td>₦40,000</td><td>₦45,000-50,000</td><td>₦50,000-55,000</td><td>₦35,000-50,000</td></tr>
<tr><td>3</td><td>Carrots</td><td>₦60,000-70,000</td><td>–</td><td>₦35,000-58,000</td><td>₦70,000-75,000</td><td>₦60,000-65,000</td><td>₦60,000-65,000</td></tr>
</table>"""


def test_coldhubs_zone_table_gives_a_bag_price_range_per_zone():
    f = load("fetch_produce_prices")
    rows = f.parse_coldhubs(COLDHUBS_REPORT, "2026-10-19", "u")
    got = {(r["crop"], r["zone"]): (r["price_low_ngn"], r["price_high_ngn"], r["package_kg"]) for r in rows}
    assert len(rows) == 10, "two crops x five zones; North-East is blank and carrots are not ours"
    assert got[("Tomato", "North Central")] == (30000, 35000, 50.0)
    assert got[("Pepper", "North West")] == (40000, 40000, 25.0)


def wfp_rows(item, unit, month, prices, state="Borno"):
    return [{"date": month, "admin1": state, "market": f"M{i}", "commodity": item, "unit": unit,
             "pricetype": "Retail", "price": p} for i, p in enumerate(prices)]


def test_wfp_uses_the_newest_month_with_enough_markets_per_kg():
    fp = load("farm_prices")
    wfp = pd.DataFrame(wfp_rows("Yam", "2.5 KG", "2026-09-15", [3250] * 10)
                       + wfp_rows("Yam", "2.5 KG", "2026-10-15", [5000] * 4)       # too few markets yet
                       + [{"date": "2026-09-15", "admin1": "Borno", "market": "Damassack (CBM)", "commodity": "Yam",
                           "unit": "2.5 KG", "pricetype": "Retail", "price": 99999}])
    (o,) = fp.wfp_observations(wfp)
    assert (o["crop"], o["price_date"], o["price_ngn_per_kg"], o["zone"], o["level"]) == \
        ("Yam", "2026-09-15", 1300, "North East", "retail")


def produce(*rows):
    cols = ["price_date", "crop", "item", "source", "level", "zone", "package", "package_kg",
            "price_low_ngn", "price_high_ngn", "evidence", "url"]
    return pd.DataFrame([dict(zip(cols, r + ("e", "u"))) for r in rows], columns=cols)


def test_a_farm_gets_its_zone_then_a_national_price_then_another_zone():
    fp = load("farm_prices")
    rep = fp.build(produce(
        ("2026-10-19", "Tomato", "Tomatoes", "ColdHubs", "bulk bag", "North West", "50 kg bag", 50, 25000, 50000),
        ("2026-10-19", "Tomato", "Tomatoes", "ColdHubs", "bulk bag", "South West", "50 kg bag", 50, 55000, 60000),
        ("2026-10-09", "Tomato", "ordinary tomatoes", "Business A.M.", "wholesale", "", "50 kg basket", 50, 45000, 45000),
        ("2026-10-09", "Tomato", "hybrid tomatoes", "Business A.M.", "wholesale", "", "25 kg crate", 25, 40000, 40000),
        ("2026-08-01", "Pepper", "Pepper", "ColdHubs", "bulk bag", "North West", "25 kg bag", 25, 20000, 25000),
    ), pd.DataFrame(wfp_rows("Tomatoes", "0.5 KG", "2026-09-15", [250] * 10)), today=date(2026, 10, 26))
    tomato = rep["crops"]["Tomato"]
    zone = {z: (p["price_ngn_per_kg"], p["basis"]) for z, p in tomato["by_zone"].items()}
    assert zone["North West"] == (750, "same zone")
    assert zone["North East"] == (500, "same zone"), "WFP's Borno shop price beats a price from elsewhere"
    assert zone["South East"] == (950, "national"), "median of the newest ColdHubs zones (750, 1150)"
    assert tomato["headline"]["where"] == "median of 2 zones"
    assert [o["item"] for o in tomato["other_items"]] == ["hybrid tomatoes"]
    assert rep["crops"]["Pepper"]["headline"] is None, "86 days old is past the 45-day limit"
    assert rep["crops"]["Cassava"]["headline"] is None and "cassava" in rep["crops"]["Cassava"]["aliases"]


def test_every_price_is_labelled_with_its_type_source_and_real_date():
    fp = load("farm_prices")
    rep = fp.build(produce(
        ("2026-10-19", "Tomato", "Tomatoes", "ColdHubs", "bulk bag", "North West", "50 kg bag", 50, 25000, 50000),
        ("2026-10-09", "Onion", "wet-season onions", "Business A.M.", "wholesale", "", "70 kg bag", 70, 95000, 95000),
    ), pd.DataFrame(wfp_rows("Yam", "2.5 KG", "2026-09-15", [3250] * 10)), today=date(2026, 10, 26))
    labels = {c: e["headline"]["label"] for c, e in rep["crops"].items() if e["headline"]}
    assert labels == {
        "Yam":    "Market price, retail · WFP Nigeria Food Price Monitor · Sep 2026",
        "Tomato": "Market price, bulk bag · ColdHubs Market Weekly · week of 19 Oct 2026",
        "Onion":  "Market price, wholesale · Business A.M. fresh-produce prices · 9 Oct 2026",
    }
    assert all(not e["headline"]["is_farm_gate"] for e in rep["crops"].values() if e["headline"])


def test_commodities_list_is_unchanged_unless_farm_crops_are_asked_for():
    from fastapi.testclient import TestClient
    import api
    client = TestClient(api.app)
    plain = client.get("/commodities").json()["commodities"]
    assert not any(c.get("price_only") for c in plain)
    with_farm = client.get("/commodities", params={"include": "farm_crops"}).json()["commodities"]
    assert with_farm[:len(plain)] == plain
    assert all(c["price_only"] and c["unit"] == "NGN/kg" and c["price_label"] for c in with_farm[len(plain):])


def test_onion_headline_is_the_crop_being_harvested_in_that_half_of_the_year():
    fp = load("farm_prices")
    assert fp.onion_item(date(2026, 10, 26)) == "wet-season onions"
    assert fp.onion_item(date(2026, 3, 1)) == "dry-season onions"
    assert fp.zone_for_state("abuja") == "North Central" and fp.zone_for_state("kano") == "North West"
