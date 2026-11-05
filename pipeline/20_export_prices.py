"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Pipeline Step 20: Export view - what buyers abroad pay, and what it costs to
get each commodity there from each zone state

For each commodity with a price abroad (data/raw/export_prices.csv, from
fetch_export_prices.py), each destination gets:
  price abroad   its newest month: the EU border value of the EU's imports
                 from Nigeria (Eurostat - landed, sea freight and insurance
                 paid; months under EU_MIN_TONNES skipped), or a neighbouring
                 market's price (FAO: Niger, Cameroon, Benin, Togo, Chad;
                 wholesale where the market has it, else retail). In naira at
                 the latest CBN euro rate, so it compares with today's prices.
  gap            price abroad vs today's Nigerian national price
  by state       each of the 12 zone states (2 per zone, as /zonal):
                   buy price     the state's price today (/zonal: measured
                                 from market data, or estimated where there
                                 is none), or an Agrolinking field quote from
                                 that state in the last FIELD_MAX_AGE_DAYS
                   freight       road distance to the exit - the nearer of
                                 Lagos and Onne ports for the EU, the market
                                 itself for West Africa - x the freight rate.
                                 An estimate (config FREIGHT_NGN_PER_KG_PER_100KM).
                   cost at exit  buy price + freight
                   room          price abroad - cost at exit: what is left for
                                 export charges, sea freight and margin (EU) or
                                 border costs and margin (West Africa)
No gap or by-state costs (the price abroad is listed for information) for:
  - what Nigeria prohibits exporting (EXPORT_PROHIBITED: maize, beans, rice,
    imported food);
  - EU products that are not the product our price is for (cashew kernels);
  - a price abroad older than MAX_AGE_MONTHS;
  - a retail price abroad (Benin, Togo, Chad report only retail) - our
    prices are wholesale.

Output: outputs/intelligence/export_prices_<date>.json, served at
GET /export-prices and GET /export-prices/{commodity}.
Never fails the pipeline. Run: python pipeline/20_export_prices.py
"""

import glob, json, math, os, sys
from datetime import date

import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import (EU_MIN_TONNES, EU_NOT_COMPARABLE, EU_PRODUCTS, WA_MARKETS, CFA_PER_EUR,
                             EXPORT_PROHIBITED, EXPORT_LAW, STATE_MARKETS, EXPORT_PORTS, ROAD_FACTOR,
                             FREIGHT_NGN_PER_KG_PER_100KM, FIELD_UNITS)

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_PATH = os.path.join(BASE, "data", "raw", "export_prices.csv")
EUR_PATH    = os.path.join(BASE, "data", "external", "cbn_eur_daily.csv")
FIELD_PATH  = os.path.join(BASE, "data", "raw", "field_prices.csv")
ZONAL_DIR   = os.path.join(BASE, "outputs", "forecasts", "zonal")
OUT_DIR     = os.path.join(BASE, "outputs", "intelligence")

MAX_AGE_MONTHS     = 6     # a price abroad older than this is listed, not compared
FIELD_MAX_AGE_DAYS = 30    # a field quote stands for its state's price this long
HISTORY_MONTHS     = 24
LEVEL_ORDER        = {"wholesale": 0, "import value (cif, eu border)": 0, "retail": 1}

ABOUT = ("Prices buyers outside Nigeria pay, and what it costs to get each commodity to them from each "
         "zone state. EU: the declared value of the EU's imports from Nigeria (Eurostat), landed at the EU "
         "border with sea freight and insurance paid - not a price at Lagos port - published about 10 weeks "
         "after the month. West Africa: market prices in neighbouring countries (FAO; national statistics "
         "offices, FEWS NET, Afrique Verte), 1-3 months old. Freight within Nigeria is an estimate. 'room' is "
         "what is left of the price abroad after buying in the state and trucking to the port or border: it "
         "must still cover export charges, sea freight or border costs, and margin.")
SOURCES = {
    "Eurostat": "Eurostat Comext DS-045409, EU imports from Nigeria by product, monthly. "
                "ec.europa.eu/eurostat - free to reuse with attribution.",
    "FAO": "FAO GIEWS Food Price Monitoring and Analysis tool (fpma.fao.org), republishing the national "
           "statistics offices of Niger, Benin, Togo, Chad and Cameroon, FEWS NET and Afrique Verte.",
    "CBN": "Central Bank of Nigeria official euro rate; 655.957 CFA francs = 1 euro (fixed).",
}


def road_km(a, b):
    """Straight-line km between (lat, lon) points x ROAD_FACTOR - an estimate of the road distance."""
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2)
    return round(2 * 6371 * math.asin(math.sqrt(h)) * ROAD_FACTOR)


def exit_for(state, region, market):
    """(exit name, km) from a state's market: the nearer export port for the EU, else the market itself."""
    _, la, lo = STATE_MARKETS[state]
    if region == "EU":
        return min(((port, road_km((la, lo), pt)) for port, pt in EXPORT_PORTS.items()), key=lambda x: x[1])
    _, mla, mlo = WA_MARKETS[market]
    return market, road_km((la, lo), (mla, mlo))


def field_state_prices(path=FIELD_PATH, today=None):
    """{(commodity, state): (NGN/t, date, note)} - the newest recent wholesale field quote per state."""
    if not os.path.exists(path):
        return {}
    f = pd.read_csv(path, parse_dates=["date"])
    if f.empty:
        return {}
    today = pd.Timestamp(today or date.today())
    unit = f["unit"].astype(str).str.strip().str.lower()
    f = f.assign(per_t=pd.to_numeric(f["price_ngn"], errors="coerce") * unit.map(FIELD_UNITS))
    f = f[(f["price_level"].astype(str).str.strip().str.lower() == "wholesale") & (f["per_t"] > 0)
          & f["state"].isin(STATE_MARKETS) & ((today - f["date"]).dt.days <= FIELD_MAX_AGE_DAYS)]
    out = {}
    for r in f.sort_values("date").itertuples():
        out[(r.commodity, r.state)] = (float(r.per_t), r.date.date().isoformat(),
                                       f"Agrolinking field quote, {r.market}: N{r.price_ngn:,.0f} per {r.unit} "
                                       f"({r.reported_by}, {r.date:%d %b %Y})")
    return out


def latest_series(df):
    """One series per (region, market, commodity): wholesale before retail, then the newest."""
    df = df.assign(_lvl=df["price_type"].str.lower().map(LEVEL_ORDER).fillna(2))
    last = (df.groupby(["region", "market", "commodity", "product", "price_type"], as_index=False)
              .agg(_last=("period", "max"), _lvl=("_lvl", "first")))
    last = last.sort_values(["_lvl", "_last"], ascending=[True, False])
    return last.drop_duplicates(["region", "market", "commodity"])


def state_rows(commodity, region, market, price_abroad, zonal_states, quotes):
    rows = []
    for state, info in zonal_states.items():
        if state not in STATE_MARKETS:
            continue
        rec = info.get(commodity)
        quote = quotes.get((commodity, state))
        if quote:
            buy, basis, note = quote[0], "field quote", quote[2]
        elif rec and rec.get("state_price"):
            src = str(rec.get("factor_source", ""))
            buy = float(rec["state_price"])
            basis = "measured" if src.startswith("measured") else "estimated"
            note = (f"national price x {rec.get('price_factor')} ({src})" if basis == "measured" else
                    f"national price x {rec.get('price_factor')} - an estimated state gap, not market data")
        else:
            continue
        exit_name, km = exit_for(state, region, market)
        freight = round(km * FREIGHT_NGN_PER_KG_PER_100KM * 10)    # NGN/kg per 100 km -> NGN/t over km
        cost = round(buy + freight)
        rows.append({"state": state, "market": STATE_MARKETS[state][0], "buy_price_ngn_t": round(buy),
                     "buy_price_basis": basis, "buy_price_note": note, "exit": exit_name, "distance_km": km,
                     "freight_ngn_t": freight, "freight_basis": "estimate", "cost_at_exit_ngn_t": cost,
                     "room_ngn_t": round(price_abroad - cost),
                     "room_pct": round((price_abroad - cost) / cost * 100, 1)})
    return sorted(rows, key=lambda r: r["cost_at_exit_ngn_t"])


def build(export, eur_daily, zonal, quotes, today):
    today = pd.Timestamp(today)
    eur_daily = eur_daily.sort_values("date")
    ngn_per_eur = float(eur_daily["ngn_per_eur"].iloc[-1])
    rate_date = str(eur_daily["date"].iloc[-1])[:10]
    rate_now = {"EUR": ngn_per_eur, "XOF": ngn_per_eur / CFA_PER_EUR, "XAF": ngn_per_eur / CFA_PER_EUR}

    ex = export[export["price_local_t"] > 0].copy()
    ex = ex[(ex["region"] != "EU") | (ex["quantity_t"] >= EU_MIN_TONNES)]
    ex = ex[(ex["region"] == "EU") | ex["market"].isin(WA_MARKETS)]
    anchors = zonal.get("national_anchors", {})
    states = {s: info for z in zonal.get("zones", {}).values() for s, info in z.get("states", {}).items()}

    out = {}
    for _, pick in latest_series(ex).iterrows():
        c, region, market = pick["commodity"], pick["region"], pick["market"]
        s = ex[(ex["region"] == region) & (ex["market"] == market) & (ex["commodity"] == c)
               & (ex["product"] == pick["product"]) & (ex["price_type"] == pick["price_type"])].sort_values("period")
        r = s.iloc[-1]
        period = pd.Timestamp(r["period"])
        months_old = (today.year - period.year) * 12 + today.month - period.month
        abroad = round(float(r["price_local_t"]) * rate_now[r["currency"]])
        national = (anchors.get(c) or {}).get("price")
        prohibited = EXPORT_PROHIBITED.get(c)
        not_comparable = EU_NOT_COMPARABLE.get(c) if region == "EU" else None
        stale = months_old > MAX_AGE_MONTHS
        retail = str(r["price_type"]).lower() == "retail"
        if prohibited:
            note = (f"Nigeria prohibits exporting {prohibited} ({EXPORT_LAW}; Nigeria Customs' export "
                    f"prohibition list). This price is shown for information only.")
        elif not_comparable:
            note = not_comparable
        elif stale:
            note = f"Newest price abroad is {months_old} months old - listed, not compared."
        elif retail:
            # Cotonou's retail sorghum ran 3x Nigerian wholesale (Aug 2026): a shop price, not a buyer's
            note = f"Retail (shop) price in {market} - listed, not compared with Nigerian wholesale prices."
        else:
            note = None
        compare = not (prohibited or not_comparable or stale or retail)
        dest = {
            "region": region, "country": r["country"], "market": market, "product": r["product"],
            "product_code": r["product_code"] if isinstance(r["product_code"], str) else "",
            "price_type": r["price_type"], "period": str(r["period"])[:7], "months_old": months_old,
            "price_local_t": round(float(r["price_local_t"]), 2), "currency": r["currency"],
            "volume_t": None if pd.isna(r["quantity_t"]) else round(float(r["quantity_t"]), 1),
            "price_ngn_t": abroad, "source": r["source_name"],
            "gap_vs_national_pct": (round((abroad / national - 1) * 100, 1) if compare and national else None),
            "compared": compare, "note": note,
            "history": [{"period": str(h["period"])[:7], "price_local_t": round(float(h["price_local_t"]), 2),
                         "price_ngn_t": None if pd.isna(h["price_ngn_t"]) else round(float(h["price_ngn_t"])),
                         "volume_t": None if pd.isna(h["quantity_t"]) else round(float(h["quantity_t"]), 1)}
                        for _, h in s.tail(HISTORY_MONTHS).iterrows()],
            "by_state": [], "cheapest_state": None,
        }
        if compare:
            dest["by_state"] = state_rows(c, region, market, abroad, states, quotes)
            if dest["by_state"]:
                best = dest["by_state"][0]
                dest["cheapest_state"] = {
                    "state": best["state"], "cost_at_exit_ngn_t": best["cost_at_exit_ngn_t"],
                    "buy_price_basis": best["buy_price_basis"],
                    "note": None if best["buy_price_basis"] != "estimated" else
                            f"{c} state prices are estimates (no market data by state yet), so this ranking "
                            f"is indicative."}
        entry = out.setdefault(c, {
            "export_allowed": not prohibited,
            "export_note": (f"Export prohibited: {prohibited} ({EXPORT_LAW})" if prohibited else None),
            "national_price_ngn_t": None if national is None else round(float(national)),
            "destinations": []})
        entry["destinations"].append(dest)
    for entry in out.values():
        entry["destinations"].sort(key=lambda d: (d["region"] != "EU", not d["compared"], d["country"], d["market"]))

    return {
        "as_of": today.date().isoformat(), "unit": "NGN/t", "about": ABOUT,
        "fx": {"ngn_per_eur": round(ngn_per_eur, 4), "ngn_per_cfa": round(ngn_per_eur / CFA_PER_EUR, 4),
               "date": rate_date, "source": SOURCES["CBN"]},
        "freight": {"ngn_per_kg_per_100km": FREIGHT_NGN_PER_KG_PER_100KM, "road_factor": ROAD_FACTOR,
                    "basis": "Estimate: one rate for all roads; distance = straight line x "
                             f"{ROAD_FACTOR}. Excludes loading, levies and border or port charges."},
        "eu_min_tonnes": EU_MIN_TONNES, "max_age_months": MAX_AGE_MONTHS, "sources": SOURCES,
        "commodities": dict(sorted(out.items())),
    }


def run():
    logger.info("=" * 60)
    logger.info("STEP 20 - EXPORT VIEW (prices abroad, cost to get there from each state)")
    logger.info("=" * 60)
    for path in (EXPORT_PATH, EUR_PATH):
        if not os.path.exists(path):
            logger.warning(f"  {os.path.relpath(path, BASE)} missing - no export view today")
            return True
    zonal_files = sorted(glob.glob(os.path.join(ZONAL_DIR, "zonal_forecast_*.json")))
    if not zonal_files:
        logger.warning("  No zonal forecast - no export view today")
        return True
    with open(zonal_files[-1], encoding="utf-8") as f:
        zonal = json.load(f)
    today = date.today()
    rep = build(pd.read_csv(EXPORT_PATH), pd.read_csv(EUR_PATH), zonal, field_state_prices(today=today), today)
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"export_prices_{rep['as_of']}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False, allow_nan=False)
    for c, e in rep["commodities"].items():
        for d in e["destinations"]:
            if d["compared"]:
                best = d["cheapest_state"] or {}
                logger.info(f"  {c:<14} {d['market'][:24]:<24} {d['period']}  N{d['price_ngn_t']:>11,.0f}/t  "
                            f"gap {d['gap_vs_national_pct']}%  cheapest from {best.get('state')} "
                            f"({best.get('buy_price_basis')})")
    logger.success(f"EXPORT VIEW DONE - {len(rep['commodities'])} commodities -> {os.path.relpath(out_path, BASE)}")
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.warning(f"Export view not built: {e}")
    sys.exit(0)
