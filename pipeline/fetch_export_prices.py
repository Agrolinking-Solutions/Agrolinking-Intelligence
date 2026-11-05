"""
AGROLINKING COMMODITY INTELLIGENCE SYSTEM
Export and neighbouring-market prices: what buyers outside Nigeria pay

Two real, official sources, saved to data/raw/export_prices.csv (rewritten
each run - both revise past months):
  - EU: Eurostat Comext DS-045409, the EU's monthly imports FROM NIGERIA by
    product: declared value (EUR) over weight = a price per tonne landed at
    the EU border (cost, insurance and freight paid), not a price at Lagos
    port. ~10 weeks late. Free to reuse with attribution. Products:
    EU_PRODUCTS in config/settings.py. Every month is kept with its tonnage;
    the export view uses a month only on EU_MIN_TONNES or more.
  - West Africa: FAO's price API (as fetch_fao_prices.py) for Niger,
    Cameroon, Benin, Togo and Chad - prices reported by their national
    statistics offices, FEWS NET and Afrique Verte, monthly, 1-3 months late.
    Commodities: WA_PRODUCTS in config/settings.py.

Naira: each month's average CBN euro rate (data/external/cbn_eur_daily.csv,
written by fetch_macro.py); CFA francs through the euro at the fixed 655.957.

Used by pipeline/20_export_prices.py. Never fails the pipeline; a source that
can't be read keeps its rows from the last run.
Run: python pipeline/fetch_export_prices.py
"""

import io, os, re, sys, time
from datetime import datetime, timezone

import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import EU_PRODUCTS, WA_COUNTRIES, WA_PRODUCTS, CFA_PER_EUR

logger.remove()
logger.add(sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}",
    level="INFO")

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE, "data", "raw", "export_prices.csv")
EUR_PATH = os.path.join(BASE, "data", "external", "cbn_eur_daily.csv")
EUROSTAT = "https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1/data/DS-045409"
FAO_API  = "https://fpma.fao.org/giews/v4/global/price_module/api/v1"
HEADERS  = {"User-Agent": "Agrolinking-Intelligence/1.0 (daily price reader; contact: john@agrolinking.com)"}
SINCE    = "2015-01"
COLUMNS  = ["period", "region", "country", "market", "commodity", "product", "product_code", "price_type",
            "price_local_t", "currency", "quantity_t", "ngn_per_currency", "price_ngn_t", "source_name",
            "retrieved_on"]


def eu_prices(sdmx_csv: str) -> pd.DataFrame:
    """Eurostat SDMX-CSV (value and 100-kg quantity rows) -> one row per product-month, EUR per tonne."""
    e = pd.read_csv(io.StringIO(sdmx_csv), dtype={"product": str})
    p = e.pivot_table(index=["product", "TIME_PERIOD"], columns="indicators", values="OBS_VALUE",
                      aggfunc="sum").reset_index()
    if not {"VALUE_IN_EUROS", "QUANTITY_IN_100KG"} <= set(p.columns):
        raise ValueError(f"Eurostat reply has no value/quantity columns: {list(p.columns)}")
    p = p[(p["QUANTITY_IN_100KG"] > 0) & p["VALUE_IN_EUROS"].notna()]
    by_code = {code: (c, name) for c, (code, name) in EU_PRODUCTS.items()}
    p = p[p["product"].isin(by_code)]
    out = pd.DataFrame({
        "period": pd.to_datetime(p["TIME_PERIOD"]).dt.strftime("%Y-%m-01"),
        "region": "EU", "country": "European Union", "market": "EU border (all member states)",
        "commodity": p["product"].map(lambda x: by_code[x][0]),
        "product": p["product"].map(lambda x: by_code[x][1]), "product_code": "CN " + p["product"],
        "price_type": "import value (CIF, EU border)",
        "price_local_t": (p["VALUE_IN_EUROS"] / (p["QUANTITY_IN_100KG"] / 10)).round(2),
        "currency": "EUR", "quantity_t": (p["QUANTITY_IN_100KG"] / 10).round(2),
        "source_name": "Eurostat Comext DS-045409 (EU imports from Nigeria)",
    })
    return out.reset_index(drop=True)


def fetch_eu() -> pd.DataFrame:
    codes = "+".join(code for code, _ in EU_PRODUCTS.values())
    url = f"{EUROSTAT}/M.EU.NG.{codes}.1.VALUE_IN_EUROS+QUANTITY_IN_100KG?startPeriod={SINCE}&format=SDMX-CSV"
    r = requests.get(url, headers=HEADERS, timeout=180)
    r.raise_for_status()
    return eu_prices(r.text)


def unit_kg(label):
    """FAO unit label -> kg ('100 kg' -> 100, 'Kg' -> 1); None for anything else (e.g. 'Coro', '1 unit')."""
    s = str(label).strip().lower()
    if s == "kg":
        return 1.0
    m = re.fullmatch(r"([\d.]+)\s*kg", s)
    return float(m.group(1)) if m else None


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=90)
    r.raise_for_status()
    return r.json()


def fetch_wa() -> pd.DataFrame:
    rows = []
    for iso3, (country, currency) in WA_COUNTRIES.items():
        keep = {}
        for s in get(f"{FAO_API}/FpmaSerieDomestic/?iso3_country_code={iso3}")["results"]:
            ours = WA_PRODUCTS.get(s["commodity_name"])
            kg = unit_kg(s.get("measure_unit_label"))
            if ours and kg and str(s.get("currency", "")).upper() == currency:
                keep[s["uuid"]] = (ours, kg, s)
        uuids = list(keep)
        for i in range(0, len(uuids), 15):
            data = get(f"{FAO_API}/FpmaSeriePrice/?uuid__in={','.join(uuids[i:i + 15])}")
            for item in (data["results"] if isinstance(data, dict) else data):
                ours, kg, s = keep[item["uuid"]]
                pts = [p for p in item["datapoints"] if p.get("price_value") is not None and p["date"] >= SINCE]
                monthly = [p for p in pts if p.get("periodicity") == "monthly"]
                for p in (monthly or pts):
                    rows.append({"period": p["date"][:7] + "-01", "region": "West Africa", "country": country,
                                 "market": s["market_name"], "commodity": ours, "product": s["commodity_name"],
                                 "product_code": "", "price_type": s["price_type"].lower(),
                                 "price_local_t": round(p["price_value"] / kg * 1000, 2), "currency": currency,
                                 "quantity_t": None, "source_name": s["source_name"]})
            time.sleep(1)
        logger.info(f"  {country}: {len(keep)} series")
    out = pd.DataFrame(rows)
    # weekly points within a month (series with no monthly figures) -> their median
    keys = ["period", "region", "country", "market", "commodity", "product", "product_code", "price_type",
            "currency", "source_name"]
    return out.groupby(keys, dropna=False, as_index=False).agg(
        price_local_t=("price_local_t", "median"), quantity_t=("quantity_t", "first"))


def to_naira(df: pd.DataFrame, eur_daily: pd.DataFrame) -> pd.DataFrame:
    """Add each month's naira rate (CBN euro monthly average; CFA through the euro) and the naira price."""
    eur = eur_daily.assign(period=pd.to_datetime(eur_daily["date"]).dt.strftime("%Y-%m-01"))
    monthly = eur.groupby("period")["ngn_per_eur"].mean()
    rate = df["period"].map(monthly)
    df = df.copy()
    df["ngn_per_currency"] = (rate / df["currency"].map({"EUR": 1.0, "XOF": CFA_PER_EUR, "XAF": CFA_PER_EUR})).round(6)
    df["price_ngn_t"] = (df["price_local_t"] * df["ngn_per_currency"]).round(0)
    return df


def run():
    logger.info("=" * 60)
    logger.info("EXPORT PRICES - EU imports from Nigeria (Eurostat), West African markets (FAO)")
    logger.info("=" * 60)
    old = pd.read_csv(OUT_PATH) if os.path.exists(OUT_PATH) else pd.DataFrame(columns=COLUMNS)
    parts = []
    for region, fetch in (("EU", fetch_eu), ("West Africa", fetch_wa)):
        try:
            got = fetch()
            logger.success(f"  {region}: {len(got):,} product-months")
            parts.append(got)
        except Exception as e:
            kept = old[old["region"] == region]
            logger.warning(f"  {region}: could not refresh ({e}) - keeping {len(kept):,} rows from the last run")
            parts.append(kept.drop(columns=["ngn_per_currency", "price_ngn_t", "retrieved_on"], errors="ignore"))
    df = pd.concat([p for p in parts if len(p)], ignore_index=True)
    if df.empty:
        logger.warning("  Nothing to save - file left unchanged")
        return True
    if os.path.exists(EUR_PATH):
        df = to_naira(df, pd.read_csv(EUR_PATH))
    else:
        logger.warning(f"  {os.path.relpath(EUR_PATH, BASE)} missing (fetch_macro.py writes it) - no naira prices")
        df["ngn_per_currency"] = df["price_ngn_t"] = None
    df["retrieved_on"] = datetime.now(timezone.utc).date().isoformat()
    df = df[COLUMNS].sort_values(["region", "commodity", "country", "market", "product", "period"])
    df.to_csv(OUT_PATH, index=False)
    for (c, m), g in df[df["price_ngn_t"].notna()].groupby(["commodity", "market"]):
        r = g.iloc[-1]
        if r["region"] == "EU" or m in ("Maradi", "Kousseri", "Cotonou"):
            logger.info(f"  {c:<15} {m:<30} {r['period'][:7]}  N{r['price_ngn_t']:>12,.0f}/t  "
                        f"({r['price_local_t']:,.0f} {r['currency']}/t)")
    logger.success(f"EXPORT PRICES DONE - {len(df):,} rows saved to {os.path.relpath(OUT_PATH, BASE)}")
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.warning(f"Export prices not refreshed: {e}")
    sys.exit(0)
