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


def test_fresh_data_without_a_fresh_record_takes_the_typical_fresh_error():
    """White maize: daily AFEX prices now, but its record is FEWS NET's, always 6+ weeks
    late - its all-history error would have made a +-41% range on a 1-week-old price."""
    fc, hist, future = champion_inputs("naive")
    fc._selection_cache["W"] = dict(fc._selection_cache["X"], accuracy_4w=70.0, accuracy_8w=60.0,
                                    accuracy_13w=50.0, accuracy_26w=30.0, error_fresh_data_pct={})
    fc._selection_cache["Y"] = dict(fc._selection_cache["W"], accuracy_4w=None, accuracy_8w=None,
                                    accuracy_13w=None, accuracy_26w=None)          # never measured
    origin = pd.Timestamp("2026-10-12")
    hw = lambda c: (c["upper_ci"][3] - c["lower_ci"][3]) / 2 / c["values"][3]       # 4 weeks ahead
    x, w, y = (fc.build_champion(k, hist, 100_000.0, origin, origin, future) for k in ("X", "W", "Y"))
    assert hw(w) == pytest.approx(hw(x)), "W takes the typical fresh-data error (X's 8%), not its own 30%"
    assert hw(y) > hw(x), "a commodity with no measured accuracy keeps the wider typical all-history error"


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


def test_one_week_is_measured_but_does_not_choose_the_method():
    """1 week is reported (target: 95% at 1 week) without changing which method wins."""
    sel = load("13_select_forecaster")
    assert 1 in sel.REPORT_HORIZONS and 1 not in sel.HORIZONS
    better = {m: (5.0, 7.0, 9.0, 14.0) for m in sel.CANDIDATES}
    better["season@1.0"] = (4.0, 5.5, 7.0, 10.0)
    summ = summ_from(better)
    week = pd.DataFrame([{"method": m, "h": 1, "mape": 1.0 if m == "naive" else 9.0, "n": 30} for m in sel.CANDIDATES])
    assert sel.choose(pd.concat([summ, week]))[0] == sel.choose(summ)[0] == "season@1.0"
    assert sel.choose_long(pd.concat([summ, week]), "naive") == sel.choose_long(summ, "naive")


def test_a_test_forecast_only_uses_prices_that_had_arrived():
    """WFP's month reaches us ~4 weeks after its date; FEWS NET ~6. Starting a test
    forecast from it earlier put the 1-month figure ~3 points too high (Nov 2026)."""
    from config.settings import PUBLICATION_LAG_DAYS
    from pipeline.drift_models import arrival_dates, known_at
    df = pd.DataFrame({"date": pd.to_datetime(["2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31",
                                               "2026-09-07", "2026-09-14"]),
                       "price_ngn_mt": [100.0, 102.0, 104.0, 106.0, 108.0, 110.0],
                       "data_source": ["WFP", "Interpolated", "Interpolated", "Interpolated", "Interpolated", "WFP"]})
    df = df.assign(arrived=arrival_dates(df))
    lag = pd.Timedelta(days=PUBLICATION_LAG_DAYS["WFP"])
    hist, last = known_at(df, pd.Timestamp("2026-09-14"))
    assert last == pd.Timestamp("2026-08-10") and hist["price_ngn_mt"].iloc[-1] == 100.0,         "September's price hadn't arrived; the filled-in rows towards it weren't known either"
    hist, last = known_at(df, pd.Timestamp("2026-09-14") + lag)
    assert last == pd.Timestamp("2026-09-14") and hist["price_ngn_mt"].iloc[-1] == 110.0
    hist, last = known_at(df, pd.Timestamp("2026-08-01"))
    assert last is None


def test_trend_is_measured_on_partner_prices_only():
    """The 26-week trend reached back into World Bank x FX estimates for ~20% of the
    Agricome crops' test forecasts; a level jump between the two read as a trend."""
    from pipeline.drift_models import weekly_growth
    dates = pd.date_range("2026-05-04", periods=27, freq="W-MON")
    src = ["Synthetic (World Bank/FAO × FX)"] * 10 + ["Agricome"] * 17
    hist = pd.DataFrame({"date": dates, "price_ngn_mt": [400.0] * 10 + [600.0] * 17, "data_source": src})
    assert weekly_growth("trend@0.5", hist) == 0.0, "flat on Agricome: no trend"
    rising = hist.assign(price_ngn_mt=[400.0] * 10 + [600.0 * 1.01 ** i for i in range(17)])
    assert weekly_growth("trend@0.5", rising) == pytest.approx(0.5 * np.log(1.01), rel=1e-6)
    assert weekly_growth("trend@0.5", hist.assign(data_source="Synthetic (World Bank/FAO × FX)")) == 0.0


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


# ── Provisional WFP month (WFP publishes a month with ~5 of ~20 markets) ─────

def wfp_months(oct_moves, thin_sep=False):
    """National monthly rows (Apr-Oct 2026) and market rows for Sep/Oct with the given Oct moves."""
    dates = pd.to_datetime([f"2026-{m:02d}-15" for m in range(4, 11)])
    monthly = pd.DataFrame({"date": dates, "commodity_std": "Eggs", "pricetype": "Retail",
                            "price_ngn_mt": 6700.0, "recent_median": 18.0,
                            "thin": [False] * 5 + [thin_sep, True]})
    sep = [7000.0, 7200.0, 6800.0, 7100.0, 6900.0]
    mk = [{"date": dates[5], "commodity_std": "Eggs", "pricetype": "Retail", "market": f"m{i}", "price_ngn_mt": p}
          for i, p in enumerate(sep)]
    mk += [{"date": dates[6], "commodity_std": "Eggs", "pricetype": "Retail", "market": f"m{i}",
            "price_ngn_mt": p * (1 + mv)} for i, (p, mv) in enumerate(zip(sep, oct_moves))]
    return monthly, pd.DataFrame(mk)


def test_partial_wfp_month_is_kept_as_provisional():
    ing = load("01_ingest")
    monthly, markets = wfp_months([-0.06, -0.14, 0.0, 0.0, -0.05])
    out = ing.provisional_months(monthly, markets)
    (key, (price, note)), = out.items()
    same_market = sum(p * (1 + m) for p, m in zip([7000, 7200, 6800, 7100, 6900], [-0.06, -0.14, 0, 0, -0.05])) / 35000
    assert key[1] == "Eggs" and price == pytest.approx(6700 * same_market) and "PROVISIONAL" in note


def test_partial_wfp_month_skipped_when_markets_disagree_or_month_before_is_thin():
    ing = load("01_ingest")
    assert ing.provisional_months(*wfp_months([-0.47, -0.16, 0.0, -0.18, 0.0])) == {}   # Oct 2026 dried fish
    assert ing.provisional_months(*wfp_months([0.0] * 5, thin_sep=True)) == {}


def test_partly_reported_recent_wfp_month_is_provisional():
    """9 Nov 2026 file: November had 14 of ~19 markets and went in as a final, scored price."""
    ing = load("01_ingest")
    dates = pd.to_datetime([f"2026-{m:02d}-15" for m in range(4, 12)])
    monthly = pd.DataFrame({"date": dates, "commodity_std": "Meat (beef)", "pricetype": "Retail",
                            "price_ngn_mt": 5.8e6, "recent_median": 19.0,
                            "n_markets": [14, 19, 19, 24, 23, 19, 17, 14],     # Apr: partial but old
                            "thin": False})
    out = ing.partly_reported_months(monthly)
    assert list(out) == [(dates[-1], "Meat (beef)", "Retail")]        # Oct at 17/19 counts as complete
    price, note = out[(dates[-1], "Meat (beef)", "Retail")]
    assert price == 5.8e6 and "PROVISIONAL: 14 of ~19" in note       # its own average is kept
    thin = monthly.assign(thin=[False] * 7 + [True], n_markets=[19] * 7 + [5])
    assert ing.partly_reported_months(thin) == {}                    # thin months: provisional_months


def test_field_quotes_become_one_weekly_national_price(tmp_path, monkeypatch):
    ing = load("01_ingest")
    p = tmp_path / "field_prices.csv"
    pd.DataFrame([
        {"date": "2026-11-03", "commodity": "Wheat", "market": "Dawanau", "state": "Kano", "price_ngn": 70000,
         "unit": "100kg bag", "price_level": "wholesale", "reported_by": "Dawanau trader 1", "notes": ""},
        {"date": "2026-11-05", "commodity": "Wheat", "market": "Dawanau", "state": "Kano", "price_ngn": 36000,
         "unit": "50kg bag", "price_level": "wholesale", "reported_by": "Dawanau trader 2", "notes": ""},
        {"date": "2026-11-05", "commodity": "Wheat", "market": "Sabon Gari", "state": "Kano", "price_ngn": 800,
         "unit": "kg", "price_level": "retail", "reported_by": "shop", "notes": ""},        # retail: skipped
        {"date": "2026-11-05", "commodity": "Wheat", "market": "Dawanau", "state": "Kano", "price_ngn": 70000,
         "unit": "bag", "price_level": "wholesale", "reported_by": "trader 3", "notes": ""},  # unknown unit: skipped
    ]).to_csv(p, index=False)
    monkeypatch.setitem(ing.PATHS, "raw_field_prices", str(p))
    out = ing.load_field_prices()
    assert len(out) == 1 and out.iloc[0]["price_ngn_mt"] == 710000.0      # median of 700K and 720K
    assert out.iloc[0]["data_source"] == "Agrolinking_primary" and "median of 2" in out.iloc[0]["notes"]


def test_provisional_months_are_never_scored():
    from config.settings import SCORED_SOURCES, PROVISIONAL_SOURCES, REAL_SOURCES
    assert PROVISIONAL_SOURCES <= REAL_SOURCES and not (PROVISIONAL_SOURCES & SCORED_SOURCES)


# ── Web page dates (the model once dated a 2023 article 30 Oct 2026) ─────────

# Daily Trust's real markup: the article's JSON-LD, then a "latest news" story dated today.
DAILY_TRUST_2023 = """<html><head><title>Kano wheat farmers hopeful amid inputs prices' hike - Daily Trust</title>
<script type="application/ld+json">{"@type": "Article", "headline": "Kano wheat farmers hopeful amid inputs prices' hike",
 "datePublished": "2023-01-08T01:21:47+01:00", "dateModified": "2023-01-08T01:21:47+01:00"}</script>
<script type="application/ld+json">{"@type": "NewsArticle", "headline": "Suspected Thugs disrupt APC primary in Zamfara",
 "datePublished": "2026-11-02T13:08:31+01:00", "dateModified": "2026-11-02T13:08:31+01:00"}</script></head></html>"""
WHEAT_URL = "https://dailytrust.com/kano-wheat-farmers-hopeful-amid-inputs-prices-hike/"
D = lambda s: pd.Timestamp(s).date()


def test_page_date_is_the_articles_not_the_latest_news_box():
    from pipeline.page_dates import page_dates
    assert page_dates(DAILY_TRUST_2023, WHEAT_URL) == (D("2023-01-08"), D("2023-01-08"))


def test_page_date_meta_tags_and_updates():
    from pipeline.page_dates import page_dates, confirm_source_date
    html = ('<meta property="article:published_time" content="2026-05-27T16:37:45+00:00">'
            '<meta property="article:modified_time" content="2026-10-12T23:04:23+00:00">')
    pub, mod = page_dates(html)
    assert (pub, mod) == (D("2026-05-27"), D("2026-10-12"))
    # a price page updated in October can carry an October price, but not a November one
    assert confirm_source_date(D("2026-10-12"), {"published": pub, "modified": mod})[0]
    assert not confirm_source_date(D("2026-11-01"), {"published": pub, "modified": mod})[0]


def web_wheat(price=690000.0):
    return {"commodity": "Wheat", "query_date": D("2026-11-01"), "price": price,
            "source_url": WHEAT_URL, "source_date": "2026-10-30", "confidence": "high",
            # national: a Kano price would be held back first (no measured Kano gap for wheat)
            "price_level": "wholesale", "market_location": "national", "source_title": "Kano wheat farmers"}


WHEAT_STATE = {"Wheat": {"hq_date": D("2026-04-13"), "direct_date": D("2026-04-13"), "wfp_date": None,
                         "real_date": D("2026-04-13"), "real_price": 706833.0}}


def test_misdated_web_price_is_not_promoted():
    pr = load("14_promote_web_prices")
    old = lambda url: {"published": D("2023-01-08"), "modified": D("2023-01-08"), "error": None}
    rows, decisions = pr.promote([web_wheat()], {k: dict(v) for k, v in WHEAT_STATE.items()},
                                 D("2026-11-01"), page_lookup=old)
    assert not rows and "wrong date" in decisions[0]["reason"]
    blocked = lambda url: {"published": None, "modified": None, "error": "HTTP 429"}
    rows, decisions = pr.promote([web_wheat()], {k: dict(v) for k, v in WHEAT_STATE.items()},
                                 D("2026-11-01"), page_lookup=blocked)
    assert not rows and "needs human review" in decisions[0]["reason"]
    current = lambda url: {"published": D("2026-10-30"), "modified": None, "error": None}
    rows, decisions = pr.promote([web_wheat()], {k: dict(v) for k, v in WHEAT_STATE.items()},
                                 D("2026-11-01"), page_lookup=current)
    assert len(rows) == 1 and decisions[0]["accepted"]


def test_a_stale_copy_does_not_corroborate_a_big_move():
    pr = load("14_promote_web_prices")
    rice = {"commodity": "Rice (foreign)", "query_date": D("2026-11-03"), "price": 1003289.0,
            "source_url": "https://www.legit.ng/rice-dealers", "source_date": "2026-11-02",
            "confidence": "medium", "price_level": "wholesale"}
    # the Lagos tracker figure dated only "October 2026": 33 days old, rejected on its own
    stale = {**rice, "price": 996241.0, "source_url": "https://lagosagric.com/price-tracker/",
             "source_date": "October 2026"}
    fresh = {**stale, "source_date": "2026-10-30"}
    # 20% below FEWS NET's 1.26M (28 Sep), so it needs a second website
    ok, reason, _ = pr.evaluate_candidate(rice, 1260667.0, D("2026-09-28"), D("2026-11-03"), [stale])
    assert not ok and "second website" in reason
    ok, _, _ = pr.evaluate_candidate(rice, 1260667.0, D("2026-09-28"), D("2026-11-03"), [fresh])
    assert ok


def test_misdated_web_price_is_not_cross_checked():
    cc = load("16_cross_check")
    master = pd.DataFrame({"commodity": "Wheat", "date": pd.to_datetime(["2026-10-26"]),
                           "price_ngn_mt": [700000.0], "data_source": "Agrolinking_primary",
                           "market_type": "wholesale"})
    old = lambda url: {"published": D("2023-01-08"), "modified": D("2023-01-08"), "error": None}
    current = lambda url: {"published": D("2026-10-30"), "modified": None, "error": None}
    assert cc.build_report(master, [web_wheat(400000.0)], D("2026-11-01"),
                           page_lookup=old)["commodities"]["Wheat"]["web_prices"] == []
    assert cc.build_report(master, [web_wheat(400000.0)], D("2026-11-01"),
                           page_lookup=current)["commodities"]["Wheat"]["web_prices"]


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


def test_no_real_price_far_out_of_line_with_the_real_prices_around_it(master):
    """WFP's Abba Gamaram 'Jan 2016' month carried 2020s prices - eggs N6,200 a crate when
    July's was N957 - and was the national price for six commodities (found 3 Nov 2026)."""
    real = master[master["data_source"].isin(REAL_SOURCES) & master["price_ngn_mt"].notna()]
    bad = []
    for c, g in real.groupby("commodity"):
        s = g.set_index("date")["price_ngn_mt"].sort_index()
        for d, p in s.items():
            near = s[(s.index != d) & (abs((s.index - d).days) <= 183)]
            if len(near) >= 2 and not 0.4 <= p / near.median() <= 2.5:
                bad.append(f"{c} {d.date()} {p:,.0f} vs {near.median():,.0f}")
    assert not bad, bad


def test_wfp_unit_slips_are_not_in_the_national_series(master):
    """Yobe's retail measures doubled Jul 2017 - May 2018 and fell back to exactly half; white beans'
    national price read 472-587K for 11 months against ~290K either side. Borno/Yobe eggs, beef, goat
    and fish halved for single months. 01_ingest WFP_UNIT_SLIPS drops them (found 5 Nov 2026)."""
    real = master[master["data_source"].isin(REAL_SOURCES) & (master["record_type"] == "historical")]
    def s(c):
        return real[real["commodity"] == c].set_index("date")["price_ngn_mt"].sort_index()
    for beans in ("Beans (white)", "Beans (red)"):
        b = s(beans)
        before = b["2017-01-01":"2017-06-30"].median()
        assert b["2017-07-01":"2018-05-31"].max() < 1.5 * before, beans
    for c, month in (("Eggs", "2017-06"), ("Eggs", "2022-04"), ("Meat (beef)", "2022-04"), ("Meat (goat)", "2022-04")):
        x = s(c)
        m = pd.Period(month)
        around = x[((x.index.to_period("M") == m - 1) | (x.index.to_period("M") == m + 1))].median()
        here = x[x.index.to_period("M") == m]
        assert here.empty or here.min() > 0.75 * around, (c, month)


def test_filled_in_prices_only_between_real_prices(master):
    """A filled-in week is drawn between two real prices. Before the first one it copied it
    back for years - white beans sat at the mis-dated Jan 2016 price all the way to 2002.
    (Estimates are fixed points too: World Bank's, and a sibling's real price x ratio.)"""
    from config.settings import SIBLING_SOURCE
    anchors = REAL_SOURCES | {"WB_RTP_estimate", SIBLING_SOURCE}
    for c, g in master.groupby("commodity"):
        a = g.loc[g["data_source"].isin(anchors), "date"]
        filled = g.loc[g["data_source"] == "Interpolated", "date"]
        if len(a) and len(filled):
            assert filled.min() >= a.min() and filled.max() <= a.max(), c


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


# ── Wrong things on the site, found 3 Nov 2026 ──────────────────────────────

def test_exchange_rate_signal_reads_the_real_column(tmp_path, monkeypatch):
    """The API looked for a 'usd_ngn' column, found none and compared a default 1,600 with the
    real average: 'NGN weakened +19.6%' when the rate had barely moved."""
    import api
    (tmp_path / "data" / "external").mkdir(parents=True)
    dates = pd.date_range("2026-08-03", periods=14, freq="W-MON")
    for latest, signal in ((1347.0, "Mid"), (1500.0, "High")):
        pd.DataFrame({"date": dates, "fx_rate_usd_ngn": [1340.0] * 13 + [latest],
                      "fx_source": "CBN official rate"}).to_csv(
            tmp_path / "data" / "external" / "fx_rates.csv", index=False)
        monkeypatch.setattr(api, "BASE_DIR", str(tmp_path))
        fx = api.forecast_factor_drivers(commodity="Wheat")["factors"]["fx_import_parity"]
        assert fx["rating"] == signal
        assert abs(fx["fx_pct_vs_avg"] - (latest / ((1340 * 12 + latest) / 13) - 1) * 100) < 0.01


def test_no_recent_real_price_means_unchecked_not_failed(monkeypatch):
    """Validation fell back to typed-in prices (fish 1.2M vs WFP's real 2.4M) and showed
    'Within target: No' for beans, beef, goat and fish. Now: unchecked, forecast unchanged."""
    v = load("06_validate")
    monkeypatch.setattr(v, "get_reference_price", lambda c: (None, "No real price in the last 45 days"))
    vals = [100.0, 101.0, 102.0]
    fc = {"last_known_price": 100.0, "last_known_date": "2026-11-02", "last_real_price": 100.0,
          "horizons": {h: {"ensemble": {"values": vals, "lower_ci": vals, "upper_ci": vals,
                                        "models_used": ["champion:naive"]},
                           "forecast_end": "2026-12-01"} for h in ("daily", "monthly")}}
    out = v.validate_commodity("Fish (dried)", fc)
    assert out["validation"]["status"] == "unchecked"
    assert out["validation"]["within_target"] is None, "None = not checked; False showed 'No'"
    assert "reference_price" not in out["validation"] and "error_pct_after" not in out["validation"]
    assert out["horizons"]["monthly"]["ensemble"]["values"] == vals
    assert out["horizons"]["monthly"]["forecast_end_detail"]["price"] == 102.0
    assert not hasattr(v, "MANUAL_PRICES") and not hasattr(v, "WEB_REFERENCE_PRICES")


def test_afex_history_uses_the_median_not_one_days_print(tmp_path, monkeypatch):
    """Yellow maize 'rose' 39% on one AFEX print (470,870, 2 Nov 2026); its 5-day median was 337,700."""
    ing = load("01_ingest")
    prints = [("2026-10-26", 279570.0), ("2026-10-27", 321510.0), ("2026-10-28", 369740.0),
              ("2026-10-30", 337700.0), ("2026-11-02", 470870.0)]
    raw = tmp_path / "raw"
    raw.mkdir()
    pd.DataFrame([{"as_of_date": d, "mansa_commodity": "maize", "country": "Nigeria",
                   "price_type": "exchange", "price_ngn_mt": p, "price_as_shown": f"{p / 1000} NGN / kg",
                   "source": "AFEX", "url": "https://www.mansamarkets.com/commodities/maize",
                   "retrieved_on": d} for d, p in prints]).to_csv(raw / "afex_prices.csv", index=False)
    monkeypatch.setitem(ing.PATHS, "raw_web_prices", str(raw / "web_prices.csv"))
    df = ing.load_afex_prices().set_index("date")["price_ngn_mt"]
    assert df[pd.Timestamp("2026-11-02")] == 337700.0
    assert df[pd.Timestamp("2026-10-30")] == pytest.approx(329605.0), "week of 26 Oct: median of its 4 days"


def test_a_corrected_price_is_not_a_market_move():
    """Withdrawn web prices made local rice the 'biggest faller' at -23% on 3 Nov 2026."""
    z = load("07_zonal_forecast")
    real_now = {"2026-10-12": 1_068_043.0, "2026-11-02": 1_025_714.0}
    # yesterday's figure stood on a price that has since been withdrawn
    kind, note = z.classify_change((1_352_040.0, "2026-10-19"), (1_025_714.0, "2026-11-02"), real_now)
    assert kind == z.CHANGE_REVISION and "withdrawn" in note
    # ...or corrected (a provisional WFP month replaced by the full month)
    kind, note = z.classify_change((1_068_043.0, "2026-10-12"), (1_068_043.0, "2026-10-12"),
                                   {"2026-10-12": 1_100_000.0})
    assert kind == z.CHANGE_REVISION and "corrected" in note
    assert z.classify_change((1_068_043.0, "2026-10-12"), (1_025_714.0, "2026-11-02"),
                             real_now)[0] == z.CHANGE_NEW_REAL
    assert z.classify_change((1_068_043.0, "2026-10-12"), (1_068_043.0, "2026-10-12"),
                             real_now)[0] == z.CHANGE_FORECAST
    assert z.classify_change(None, (1.0, "2026-11-02"), real_now) == (None, None)


def test_one_states_price_is_not_a_national_price():
    """Four Lagos prices stood as national prices, and a Lagos 50kg-bag price opened alert #7
    against AFEX's national maize price (Nov 2026)."""
    from pipeline.locations import state_of, national_equivalent
    assert state_of("Mile 12/Mushin/Daleko/Oyingbo, Lagos") == "Lagos"
    assert state_of("Maiduguri, Borno State") == "Borno"
    assert state_of("Minna, Niger State") == "Niger"
    for national in ("national", "Lagos and Kano markets", "Niger Delta", "Nigeria", None):
        assert state_of(national) is None
    nat, state, how = national_equivalent("Maize (yellow)", 1_177_000.0, "Mile 12, Lagos")
    assert state == "Lagos" and nat == pytest.approx(1_000_000.0) and "measured" in how
    assert national_equivalent("Rice (local)", 1_025_714.0, "Lagos")[0] is None
    assert national_equivalent("Maize (white)", 450_000.0, "national") == (450_000.0, None, None)


def test_state_price_without_measured_gap_is_not_promoted_or_compared():
    pr, cc = load("14_promote_web_prices"), load("16_cross_check")
    lagos_rice = {"commodity": "Rice (local)", "query_date": D("2026-11-03"), "price": 1_040_000.0,
                  "source_url": "https://example.ng/rice", "source_title": "t", "source_date": "2026-11-02",
                  "confidence": "medium", "price_level": "wholesale", "market_location": "Lagos"}
    state = {"Rice (local)": {"hq_date": D("2026-09-14"), "direct_date": None, "wfp_date": D("2026-09-14"),
                              "real_date": D("2026-09-14"), "real_price": 1_057_286.0}}
    rows, decisions = pr.promote([lagos_rice], state, D("2026-11-03"), page_lookup=lambda u: {})
    assert not rows and "stand for the national price" in decisions[0]["reason"]
    master = pd.DataFrame({"commodity": "Rice (local)", "date": pd.to_datetime(["2026-10-26"]),
                           "price_ngn_mt": [1_057_286.0], "data_source": "WFP", "market_type": "wholesale"})
    current = lambda url: {"published": D("2026-11-02"), "modified": None, "error": None}
    e = cc.build_report(master, [lagos_rice], D("2026-11-03"), page_lookup=current)["commodities"]["Rice (local)"]
    assert e["web_prices"] == [] and e["other_web_prices"][0]["not_compared"]


def test_state_price_with_measured_gap_is_converted():
    pr = load("14_promote_web_prices")
    lagos_maize = {"commodity": "Maize (yellow)", "query_date": D("2026-11-03"), "price": 400_180.0,
                   "source_url": "https://example.ng/maize", "source_title": "t", "source_date": "2026-11-02",
                   "confidence": "medium", "price_level": "wholesale", "market_location": "Mile 12, Lagos"}
    state = {"Maize (yellow)": {"hq_date": D("2026-08-01"), "direct_date": D("2026-08-01"), "wfp_date": None,
                                "real_date": D("2026-10-26"), "real_price": 340_000.0}}
    current = lambda url: {"published": D("2026-11-02"), "modified": None, "error": None}
    rows, decisions = pr.promote([lagos_maize], state, D("2026-11-03"), page_lookup=current)
    assert len(rows) == 1 and float(rows[0][2]) == pytest.approx(340_000.0, abs=1)   # 400,180 / 1.177
    assert "measured Lagos gap" in rows[0][13]


SURVEY_URL = ("https://nairametrics.com/2026/11/01/"
              "lagos-food-prices-maize-ogbono-and-garri-record-sharp-increases-in-october/")


def test_a_page_found_to_be_shop_prices_is_listed_not_compared_or_promoted():
    """Nairametrics' Lagos food-price survey (50kg maize bag N55,000 = N1.1m/t), read as
    wholesale, opened alerts #7 and #9 at 2.6x AFEX (Nov 2026). web_sources_levels.csv"""
    pr, cc = load("14_promote_web_prices"), load("16_cross_check")
    survey = {"commodity": "Maize (white)", "query_date": D("2026-11-03"), "price": 1_100_000.0,
              "source_url": SURVEY_URL, "source_title": "t", "source_date": "2026-10-01", "confidence": "high",
              "price_level": "wholesale", "market_location": "Mile 12, Mushin, Daleko, Oyingbo markets, Lagos"}
    master = pd.DataFrame({"commodity": "Maize (white)", "date": pd.to_datetime(["2026-11-02"]),
                           "price_ngn_mt": [416_520.0], "data_source": "AFEX", "market_type": "wholesale"})
    current = lambda url: {"published": D("2026-11-01"), "modified": None, "error": None}
    e = cc.build_report(master, [survey], D("2026-11-03"), page_lookup=current)["commodities"]["Maize (white)"]
    assert e["status"] == "no web price" and e["other_web_prices"][0]["level"] == "retail"
    assert any("shop (retail) price" in n for n in e["notes"])
    state = {"Maize (white)": {"hq_date": D("2026-08-01"), "direct_date": D("2026-08-01"), "wfp_date": None,
                               "real_date": D("2026-08-01"), "real_price": 378_500.0}}
    rows, decisions = pr.promote([survey], state, D("2026-11-03"), page_lookup=current)
    assert not rows and "retail" in decisions[0]["reason"] and decisions[0]["level_note"]
    # only that survey: other Nairametrics pages keep the level the model gave
    other = {**survey, "source_url": "https://nairametrics.com/2026/06/02/nbs-garri-prices-tumble/"}
    assert pr.with_source_level(other, pr.load_source_levels()) == other


# ── Measuring and reporting ──────────────────────────────────────────────────

def scored_rows(rows):
    return pd.DataFrame([{"made_on": "2026-10-13", "commodity": "Sesame", "horizon": "weekly",
                          "target_date": pd.Timestamp("2026-10-20"), "actual_date": pd.Timestamp("2026-10-19"),
                          "method": "champion", "valid": True, "predicted": p, "anchor_price": a,
                          "lower": lo, "upper": hi, "actual": act} for p, a, lo, hi, act in rows])


def test_live_range_coverage_and_no_direction_for_price_holds():
    """Ranges aim to hold the real price 8 times in 10 - now checked on published forecasts.
    A 'price holds' forecast has no direction; counting it as a miss put direction at 6%."""
    t = load("15_track_accuracy")
    rows = scored_rows([(100.0, 100.0, 90.0, 110.0, 105.0),     # flat, inside
                        (100.0, 100.0, 95.0, 105.0, 120.0),     # flat, outside
                        (110.0, 100.0, 100.0, 120.0, 115.0),    # called up, went up, inside
                        (110.0, 100.0, np.nan, np.nan, 90.0)])  # called up, went down, no range
    _, overall = t.summarise(rows)
    s = overall["1 week"]["champion"]
    assert s["in_range_pct"] == pytest.approx(66.7, abs=0.1) and s["ranges_scored"] == 3
    assert s["direction_scored"] == 2 and s["direction_hit_pct"] == 50.0
    assert s["price_holds_pct"] == 50.0


def test_live_forecast_is_never_scored_against_a_price_known_when_it_was_made():
    """A 1-week forecast made on Monday 19 Oct was scored against that Monday's own
    price (its starting point): 113 of 301 champion forecasts in Nov 2026."""
    t = load("15_track_accuracy")
    real = pd.DataFrame({"commodity": "Sesame", "data_source": "Agricome", "price_ngn_mt": [100.0, 110.0],
                         "date": pd.to_datetime(["2026-10-19", "2026-10-27"])})
    log = pd.DataFrame({"made_on": ["2026-10-19", "2026-10-26"], "commodity": "Sesame", "horizon": "weekly",
                        "target_date": ["2026-10-26", "2026-11-02"], "predicted": 100.0})
    out = t.attach_actuals(log, real)
    # 19 Oct -> 26 Oct: 19 Oct and 27 Oct are both within 7 days; only 27 Oct came after
    assert out.loc[0, "actual_date"] == pd.Timestamp("2026-10-27") and out.loc[0, "actual"] == 110.0
    # 26 Oct -> 2 Nov: 27 Oct is after the forecast day, so it counts
    assert out.loc[1, "actual"] == 110.0
    same_day = t.attach_actuals(log.iloc[:1].assign(made_on="2026-10-27"), real)
    assert pd.isna(same_day.loc[0, "actual"]), "a price from the forecast day itself is not a test"


def test_live_record_counts_distinct_real_prices():
    t = load("15_track_accuracy")
    rows = scored_rows([(100.0, 100.0, 90.0, 110.0, 105.0)] * 5)    # five forecast days, one real price
    _, overall = t.summarise(rows)
    s = overall["1 week"]["champion"]
    assert s["scored"] == 5 and s["real_prices"] == 1


def test_headline_and_fair_audit_use_the_same_rules():
    """The site's figure (13_select_forecaster) and the weekly fair audit must leave out
    the same commodities and count 'fresh data' the same way, or they can't be compared."""
    sel = load("13_select_forecaster")
    spec = importlib.util.spec_from_file_location("audit", os.path.join(ROOT, "scripts", "research", "forecast_audit.py"))
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    assert sel.STALE_WEEKS == audit.STALE_WEEKS == 12, "a real price over 12 weeks old forecasts at ~34%"
    for k in ("FRESH_WEEKS", "GAP_WEEKS", "WINDOW_WEEKS", "MIN_GAIN", "TIE_MARGIN", "LONG_MIN_GAIN",
              "HORIZONS", "HORIZON_W", "REPORT_HORIZONS"):
        assert getattr(sel, k) == getattr(audit, k), k


def test_monthly_report_is_written_once_per_month(tmp_path):
    r = load("18_accuracy_report")
    assert r.report_due(D("2026-11-03"), str(tmp_path)) is None, "first report is for November 2026"
    assert r.report_due(D("2026-12-01"), str(tmp_path)) == "2026-11"
    (tmp_path / "accuracy_2026-11.md").write_text("done")
    assert r.report_due(D("2026-12-02"), str(tmp_path)) is None, "a month is never redone"
    log = scored_rows([(100.0, 100.0, 90.0, 110.0, 105.0), (100.0, 100.0, 95.0, 105.0, 120.0)])
    master = pd.DataFrame({"commodity": "Sesame", "date": pd.to_datetime(["2026-10-19"]),
                           "price_ngn_mt": [105.0], "data_source": "Agricome"})
    rep, md = r.build_report("2026-10", log, {}, {"Sesame": {"accuracy_4w": 92.9}}, master)
    assert rep["live"]["1 week"]["in_range_pct"] == 50.0 and rep["live"]["1 month"] is None
    assert "nothing to score" in md and "| Sesame |" in md and "Agricome" in md


def test_feed_alert_opens_updates_and_closes():
    """Agricome's last prices were for the week of 19 Oct 2026 and nothing said so."""
    f = load("19_feed_alerts")
    master = pd.DataFrame({"commodity": ["Sesame", "Maize (yellow)"],
                           "date": pd.to_datetime(["2026-10-19", "2026-11-02"]),
                           "price_ngn_mt": [1_520_000.0, 337_700.0], "data_source": ["Agricome", "AFEX"]})
    status = f.feed_status(master, D("2026-11-03"))
    assert status["Agricome"]["days"] == 15 and status["Agricome"]["resting"] == ["Sesame"]
    acts = f.plan(status, [], D("2026-11-03"))
    assert [(a["do"], a["feed"]) for a in acts] == [("open", "Agricome")]
    issue = {"title": f.title("Agricome"), "number": 9, "body": acts[0]["body"]}
    assert f.plan(status, [issue], D("2026-11-03")) == [], "same text: nothing to do"
    later = f.feed_status(master, D("2026-11-04"))
    assert f.plan(later, [issue], D("2026-11-04"))[0]["do"] == "update"
    back = f.feed_status(pd.concat([master, master.iloc[[0]].assign(date=pd.Timestamp("2026-11-02"))]),
                         D("2026-11-04"))
    assert f.plan(back, [issue], D("2026-11-04"))[0]["do"] == "close"


def test_afex_maize_is_white_and_yellow_is_a_labelled_estimate():
    """Mansa Markets, 3 Nov 2026: AFEX's maize is white maize. It had been yellow maize's real price
    since 15 Oct. Yellow maize (no real price since Jan 2023) is now white's real price x 1.023,
    labelled an estimate, and replaces World Bank's yellow proxy (691K in Jul 2026 vs real ~377K)."""
    from config.settings import SIBLING_PRICES, SIBLING_SOURCE
    from pipeline.fetch_afex_prices import MANSA_TO_OURS
    assert load("01_ingest").AFEX_AS_REAL == {"maize": ["Maize (white)"]}
    assert MANSA_TO_OURS["maize"] == ["Maize (white)"]
    assert SIBLING_SOURCE not in REAL_SOURCES
    ratio = SIBLING_PRICES["Maize (yellow)"][1]
    clean = load("02_clean")
    df = pd.DataFrame({
        "commodity": ["Maize (yellow)", "Maize (yellow)", "Maize (white)", "Maize (white)", "Maize (yellow)"],
        "date": pd.to_datetime(["2023-01-09", "2026-07-27", "2026-07-20", "2026-11-02", "2026-11-02"]),
        "price_ngn_mt": [220_420.0, 690_974.0, 377_750.0, 416_520.0, 999.0],
        "data_source": ["WFP", "WB_RTP_estimate", "FEWS_NET", "AFEX", "Interpolated"],
        "record_type": "historical", "notes": "", "source": ""})
    y = clean.add_sibling_estimates(df).query("commodity == 'Maize (yellow)'").set_index("date")
    assert list(y["data_source"]) == ["WFP", SIBLING_SOURCE, SIBLING_SOURCE]
    assert y.loc["2026-11-02", "price_ngn_mt"] == pytest.approx(416_520 * ratio)
    assert "no real Maize (yellow) price since 2023-01-09" in y.loc["2026-11-02", "notes"]
    # rebuilt every run, never stacked; and only after the commodity's own real price
    again = clean.add_sibling_estimates(clean.add_sibling_estimates(df))
    assert (again["data_source"] == SIBLING_SOURCE).sum() == 2
    # a recent real price of its own (within 45 days) is never overwritten or estimated over...
    fresh = pd.concat([df, df.iloc[[0]].assign(date=pd.Timestamp("2026-10-26"), data_source="Web_search")])
    out = clean.add_sibling_estimates(fresh).query("commodity == 'Maize (yellow)'").set_index("date")
    assert out.loc["2026-11-02", "data_source"] != SIBLING_SOURCE
    # ...but one real price doesn't undo the estimates for the years before it
    assert out.loc["2026-07-20", "data_source"] == SIBLING_SOURCE
    # and no estimates before its first real price (history isn't invented), unless it has none
    early = df.assign(date=pd.to_datetime(["2026-08-03", "2026-07-27", "2026-07-20", "2026-11-02", "2026-11-02"]))
    assert list(clean.add_sibling_estimates(early).query("data_source == @SIBLING_SOURCE")["date"]) == [
        pd.Timestamp("2026-11-02")]
    nothing_own = df[df.commodity != "Maize (yellow)"]
    assert (clean.add_sibling_estimates(nothing_own)["data_source"] == SIBLING_SOURCE).sum() == 2


def test_frozen_afex_price_is_listed_not_compared():
    """AFEX sesame showed 2,300/kg on all 16 trading days from 14 Oct 2026 (the page says it updates
    daily) and kept a '+51% disagree' alert open against Agricome's 1.52M. A price unchanged for
    FROZEN_DAYS trading days is a standing listing: listed for reference, never compared or used."""
    from pipeline.afex_median import unchanged_run, FROZEN_DAYS
    days = pd.bdate_range("2026-10-14", periods=16)
    frozen = pd.DataFrame({"as_of_date": days.strftime("%Y-%m-%d"), "price_ngn_mt": 2_300_000.0})
    assert unchanged_run(frozen) == (16, "2026-10-14")
    assert unchanged_run(frozen, days[4])[0] == 5 < FROZEN_DAYS
    moving = frozen.assign(price_ngn_mt=[400_000.0 + i for i in range(16)])
    assert unchanged_run(moving)[0] == 1
    cc = load("16_cross_check")
    rows = [{"as_of_date": d, "mansa_commodity": "sesame", "price_type": "exchange", "price_ngn_mt": 2_300_000.0,
             "price_as_shown": "2,300 NGN / kg", "url": "https://www.mansamarkets.com/commodities/sesame"}
            for d in frozen["as_of_date"]]
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "afex.csv")
        pd.DataFrame(rows).to_csv(path, index=False)
        cand = [c for c in cc.afex_candidates(path) if c["commodity"] == "Sesame"][0]
    assert cand["price_level"] not in cc.COMPARE_LEVELS and "not changed in 16 trading days" in cand["note"]
