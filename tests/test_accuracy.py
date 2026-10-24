"""
Accuracy tests - the rules that keep prices and forecasts honest, and the
data itself. Run daily by the pipeline (step "Tests") and locally with:

    pytest tests/ -v

Rule tests use small made-up data. Data tests read the current master
dataset and fail when a known class of error comes back (CFA-franc border
prices, the duplicated WFP extract, future-dated or non-positive real prices).
"""

import os, sys, importlib.util

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config.settings import PATHS, COMMODITIES, REAL_SOURCES, PARTNER_SOURCES


def load(step):
    """Import a pipeline step whose file name starts with a number."""
    spec = importlib.util.spec_from_file_location(step, os.path.join(ROOT, "pipeline", f"{step}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Forecast ranges ──────────────────────────────────────────────────────────

def test_range_widens_with_horizon_and_data_age():
    from pipeline.drift_models import range_halfwidth
    weeks = np.array([4, 8, 13, 26])
    fresh = range_halfwidth(np.full(4, 8.0), weeks, 0)
    old = range_halfwidth(np.full(4, 8.0), weeks, 20)
    assert np.all(np.diff(fresh) > 0), "same error further out must give a wider range"
    assert np.all(old > fresh), "old data must give a wider range"
    assert fresh[0] == pytest.approx(0.08 * 1.69), "1-month fresh range = 1.69 x measured error"


def test_range_never_narrower_further_out():
    from pipeline.drift_models import range_halfwidth
    weeks = np.arange(1, 27)
    err = np.interp(weeks, [4, 8, 26], [2.8, 3.0, 4.3])   # Wheat, 26 weeks without a real price
    hw = range_halfwidth(err, weeks, 26)
    assert np.all(np.diff(hw) >= 0)


def test_range_has_floor_and_cap():
    from pipeline.drift_models import range_halfwidth, RANGE_MAX
    assert range_halfwidth(0.1, 4, 0) == pytest.approx(0.02 * 1.69), "error floor of 2%"
    assert range_halfwidth(90.0, 26, 52) == pytest.approx(RANGE_MAX)


# ── Forecast start ───────────────────────────────────────────────────────────

def champion_inputs(method="infl@0.5"):
    fc = load("05_forecast")
    fc.CHAMPION_MODE = "live"
    fc._selection_cache = {"X": {"method": method, "long_method": None, "accuracy_4w": 92.0, "accuracy_8w": 89.0,
                                 "accuracy_13w": 86.0, "accuracy_26w": 80.0, "n_4w": 30,
                                 "error_fresh_data_pct": {"4": 8.0, "8": 11.0, "13": 14.0, "26": 20.0}}}
    d = pd.date_range("2025-01-06", "2026-09-14", freq="W-MON")
    hist = pd.DataFrame({"date": d, "price_ngn_mt": 100_000.0, "data_source": "WFP", "food_inflation_yoy": 20.0})
    future = pd.DataFrame({"date": pd.date_range("2026-10-19", periods=26, freq="W-MON")})
    return fc, hist, future


def test_forecast_built_from_real_price_and_never_compounds():
    fc, hist, future = champion_inputs()
    real_date, origin = pd.Timestamp("2026-09-14"), pd.Timestamp("2026-10-12")
    a = fc.build_champion("X", hist, 100_000.0, real_date, origin, future)
    b = fc.build_champion("X", hist, 100_000.0, real_date, origin, future)
    assert a["anchor_price"] == b["anchor_price"], "the same real price gives the same start, run after run"
    assert 100_000 < a["anchor_price"] < 101_500, "carried 4 weeks forward at half of 20% inflation"


def test_ranges_count_from_this_week_and_widen_with_data_age():
    fc, hist, future = champion_inputs("naive")
    origin = pd.Timestamp("2026-10-12")
    fresh = fc.build_champion("X", hist, 100_000.0, origin, origin, future)
    aged = fc.build_champion("X", hist, 100_000.0, origin - pd.Timedelta(weeks=8), origin, future)
    hw = lambda c: (c["upper_ci"][0] - c["lower_ci"][0]) / 2 / c["values"][0]
    assert hw(aged) > hw(fresh) * 1.1, "8-week-old data gets the wider 'recent' range"


# ── Method selection ─────────────────────────────────────────────────────────

def summ_from(mapes):
    """{method: (m4, m8, m13, m26)} -> the summary table 13_select_forecaster scores on."""
    rows = [{"method": m, "h": h, "mape": v, "n": 30}
            for m, vals in mapes.items() for h, v in zip((4, 8, 13, 26), vals)]
    return pd.DataFrame(rows)


def test_selection_keeps_price_holds_unless_clearly_better():
    sel = load("13_select_forecaster")
    base = {m: (5.0, 7.0, 9.0, 14.0) for m in sel.CANDIDATES}
    best, _, _ = sel.choose(summ_from(base))
    assert best in ("naive", sel.TIE_METHOD)
    better = dict(base, **{"season@1.0": (4.0, 5.5, 7.0, 10.0)})
    best, _, _ = sel.choose(summ_from(better))
    assert best == "season@1.0"


def test_long_range_method_never_naive_and_needs_a_real_gain():
    sel = load("13_select_forecaster")
    base = {m: (5.0, 7.0, 9.0, 14.0) for m in sel.CANDIDATES}
    assert sel.choose_long(summ_from(base), "naive") is None
    gain = dict(base, **{"trend@0.5": (5.5, 7.5, 9.0, 11.0)})
    assert sel.choose_long(summ_from(gain), "naive") == "trend@0.5"


# ── Cross-check and alerts ───────────────────────────────────────────────────

def test_cross_check_thresholds():
    cc = load("16_cross_check")
    assert cc.status_for(0.10) == "agree"
    assert cc.status_for(-0.20) == "check"
    assert cc.status_for(0.31) == "disagree"


def test_afex_ginger_is_listed_not_compared(tmp_path):
    cc = load("16_cross_check")
    p = tmp_path / "afex.csv"
    pd.DataFrame([{"as_of_date": "2026-10-15", "mansa_commodity": "ginger", "country": "Nigeria",
                   "price_type": "exchange", "price_ngn_mt": 990000.0, "price_as_shown": "990 NGN / kg",
                   "source": "AFEX", "url": "https://www.mansamarkets.com/commodities/ginger",
                   "retrieved_on": "2026-10-16"}]).to_csv(p, index=False)
    cands = cc.afex_candidates(str(p))
    assert cands and all(c["price_level"] not in cc.COMPARE_LEVELS for c in cands)


def alert_report(gap, status="disagree", run_date="2026-10-16"):
    e = {"status": status, "gap_pct": gap, "our_price": 1_400_000, "our_date": "2026-10-12",
         "our_source": "Agricome", "our_level": "wholesale", "web_median": 2_300_000,
         "compared_with": "AFEX exchange", "notes": [], "other_web_prices": [],
         "web_prices": [{"price": 2_300_000, "site": "mansamarkets.com", "url": "https://x",
                         "source_date": "2026-10-15", "level": "exchange"}]}
    return {"run_date": run_date, "commodities": {"Sesame": e}}


def test_alerts_open_stay_quiet_email_and_close():
    pa = load("17_price_alerts")
    issues = []

    def run(rep):
        acts = pa.plan(rep, issues)
        for a in acts:
            if a["do"] == "open":
                issues.append({"number": 1, "body": a["body"]})
            elif a["do"] in ("comment", "update"):
                issues[0]["body"] = a["body"]
            elif a["do"] == "close":
                issues.clear()
        return [a["do"] for a in acts]

    assert run(alert_report(64.3)) == ["open"]
    assert run(alert_report(64.3)) == ["update"]          # same gap: no email
    assert run(alert_report(70.0)) == ["update"]          # moved < 10 points: no email
    assert run(alert_report(80.0)) == ["comment"]         # moved 10+ points: email
    assert run(alert_report(None, "no web price")) == []  # nothing to compare: leave it
    assert run(alert_report(28.0, "check")) == ["update"]  # under 30% but not under 25%: stays open, quietly
    assert run(alert_report(-20.0, "check")) == ["close"]
    assert run(alert_report(40.0)) == ["open"]


def test_alert_closes_after_a_week_with_nothing_to_compare():
    pa = load("17_price_alerts")
    issues = []
    for a in pa.plan(alert_report(64.3, run_date="2026-10-16"), issues):
        issues.append({"number": 1, "body": a["body"]})
    assert pa.plan(alert_report(None, "no web price", run_date="2026-10-20"), issues) == []
    acts = pa.plan(alert_report(None, "no web price", run_date="2026-10-23"), issues)
    assert [a["do"] for a in acts] == ["close"]


def afex_file(tmp_path, slug, prices):
    p = tmp_path / "afex.csv"
    pd.DataFrame([{"as_of_date": f"2026-10-{d:02d}", "mansa_commodity": slug, "country": "Nigeria",
                   "price_type": "exchange", "price_ngn_mt": v, "price_as_shown": "", "source": "AFEX",
                   "url": f"https://www.mansamarkets.com/commodities/{slug}", "retrieved_on": "2026-10-17"}
                  for d, v in prices]).to_csv(p, index=False)
    return str(p)


def test_afex_uses_the_middle_of_recent_days(tmp_path):
    cc = load("16_cross_check")
    c = cc.afex_candidates(afex_file(tmp_path, "soybean", [(14, 597820.0), (15, 645340.0), (16, 548540.0)]))
    assert c and c[0]["price"] == 597820.0 and c[0]["afex_days"] == 3


def test_web_copies_of_directly_read_sites_are_not_used():
    cc, pr = load("16_cross_check"), load("14_promote_web_prices")
    web = {"commodity": "Maize (white)", "query_date": "2026-10-18", "price": 389680.0,
           "source_url": "https://www.mansamarkets.com/commodities", "source_date": "2026-10-16",
           "confidence": "medium", "price_level": "exchange", "market_location": "national"}
    master = pd.DataFrame({"commodity": "Maize (white)", "date": pd.to_datetime(["2026-10-05"]),
                           "price_ngn_mt": [450000.0], "data_source": "Web_search", "market_type": "wholesale"})
    rep = cc.build_report(master, [web], pd.Timestamp("2026-10-19").date())
    assert rep["commodities"]["Maize (white)"]["web_prices"] == []
    rows, decisions = pr.promote([web], {}, pd.Timestamp("2026-10-19").date())
    assert not rows and "read directly" in decisions[0]["reason"]


# ── Daily data checks ────────────────────────────────────────────────────────

def series(prices, source="Agricome", start="2026-01-05", market="wholesale"):
    d = pd.date_range(start, periods=len(prices), freq="W-MON")
    return pd.DataFrame({"date": d, "price_ngn_mt": prices, "data_source": source, "market_type": market})


def test_unit_error_is_a_problem():
    dc = load("data_checks")
    ok = series([500_000] * 10)
    per_kg_typo = series([500_000] * 9 + [500_000_000])
    assert dc.check_unit(ok)["status"] == "ok"
    assert dc.check_unit(per_kg_typo)["status"] == "problem"


def test_one_off_spike_is_flagged():
    dc = load("data_checks")
    s = series([500_000, 510_000, 900_000, 515_000, 520_000])
    assert dc.check_spikes(s, pd.Timestamp("2026-03-01").date())["status"] == "check"


def test_shop_prices_in_a_wholesale_series_are_flagged():
    dc = load("data_checks")
    mixed = pd.concat([series([500_000] * 4), series([550_000] * 4, start="2026-03-02", market="retail")])
    today = pd.Timestamp("2026-06-01").date()
    assert dc.check_basis("Beans (red)", mixed, today)["status"] == "check"
    assert dc.check_basis("Eggs", mixed, today)["status"] == "ok"   # shop prices allowed, labelled


def test_forecast_outside_its_range_is_a_problem():
    dc = load("data_checks")
    fc = {"last_known_price": 100.0, "horizons": {"monthly": {"ensemble": {
        "values": [101.0, 130.0], "lower_ci": [95.0, 96.0], "upper_ci": [105.0, 110.0]}}}}
    assert dc.check_sane(fc, {"accuracy_4w": 92})["status"] == "problem"


# ── Ingest rules ─────────────────────────────────────────────────────────────

def test_older_wfp_extract_is_not_counted_twice():
    ing = load("01_ingest")
    months = pd.date_range("2020-01-06", periods=24, freq="MS") + pd.offsets.Week(weekday=0)
    current = pd.DataFrame({"commodity": "Beans (red)", "date": months[:20], "price_ngn_mt": 1000.0,
                            "data_source": "WFP"})
    legacy = pd.DataFrame({"commodity": "Beans (red)", "date": months + pd.Timedelta(days=7),
                           "price_ngn_mt": 1080.0, "data_source": "Agricome", "source": ing.LEGACY_WFP_SOURCE,
                           "market_type": "retail", "notes": ""})
    out = ing.place_legacy_wfp(legacy, current)
    assert len(out) == 4, "only the 4 months the current data lacks are kept"
    assert (out["data_source"] == "WFP").all()
    assert out["price_ngn_mt"].round(6).eq(1000.0).all(), "scaled to the current level"


# ── The data itself ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def master():
    if not os.path.exists(PATHS["master"]):
        pytest.skip("no master dataset")
    return pd.read_csv(PATHS["master"], parse_dates=["date"], low_memory=False)


def test_one_row_per_commodity_and_week(master):
    assert not master.duplicated(["commodity", "date"]).any()


def test_real_prices_positive_and_not_in_the_future(master):
    real = master[master["data_source"].isin(REAL_SOURCES)]
    assert (real["price_ngn_mt"] > 0).all()
    assert real["date"].max() <= pd.Timestamp.today().normalize() + pd.Timedelta(days=7)


def test_every_commodity_has_real_prices(master):
    have = set(master.loc[master["data_source"].isin(PARTNER_SOURCES), "commodity"])
    assert set(COMMODITIES) <= have, f"no partner price at all: {set(COMMODITIES) - have}"


def test_no_older_wfp_extract_labelled_agricome(master):
    bad = master[(master["data_source"] == "Agricome") & (master["source"].astype(str) == "WFP Nigeria Food Prices")]
    assert bad.empty, f"{len(bad)} WFP rows labelled Agricome"


def test_no_cfa_franc_border_prices(master):
    """Pre-2016 white maize ran ~N60K/t at Kano; CFA-franc border rows doubled it."""
    mz = master[(master["commodity"] == "Maize (white)") & master["data_source"].isin(PARTNER_SOURCES)
                & (master["date"].dt.year == 2005)]
    if mz.empty:
        pytest.skip("no 2005 white maize")
    assert mz["price_ngn_mt"].median() < 100_000


def test_day_change_is_measured_against_the_last_published_price(tmp_path):
    """Yellow maize fell 15% on AFEX on 23 Oct 2026 and the site showed 0.0%: the change was the
    slope of today's forecast curve. It is now today's price vs the price published before today."""
    import json
    from datetime import datetime
    z = load("07_zonal_forecast")
    z.ZONAL_DIR = str(tmp_path)
    for day, price in (("2026-10-22", 319930.0), ("2026-10-23", 271940.0), ("2026-10-24", 999.0)):
        with open(tmp_path / f"zonal_forecast_{day}.json", "w") as f:
            json.dump({"national_anchors": {"Maize (yellow)": {"price": price}}}, f)
    prices, day = z.previous_published(datetime(2026, 10, 24))
    assert day == "2026-10-23", "today's own file (a rerun) is never the comparison"
    assert prices["Maize (yellow)"] == 271940.0
