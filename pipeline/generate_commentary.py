"""
Agrolinking Daily Market Commentary Generator
Uses Google Gemini 1.5 Flash API (free tier - 1500 requests/day).
Get your free API key at: aistudio.google.com/app/apikey

Set environment variable before running:
  $env:GEMINI_API_KEY = "your-key-here"

Called from 08_intelligence.py after intelligence is saved.
"""

import os
import json
import glob
import urllib.request
from datetime import datetime

BASE      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTEL_DIR = os.path.join(BASE, "outputs", "intelligence")
ALERT_DIR = os.path.join(BASE, "outputs", "daily_alerts")


def generate_commentary(intel: dict, run_date: datetime) -> str:
    """Generate market commentary using Gemini 1.5 Flash (free)."""

    fpi        = intel.get("food_price_index", {}).get("value", 100)
    outlook    = intel.get("outlook_30d", {}).get("avg_pct_change", 0)
    movers     = intel.get("market_movers", {})
    riser      = movers.get("biggest_riser", {})
    faller     = movers.get("biggest_faller", {})
    alerts     = intel.get("early_warning_alerts", {}).get("summary", {})
    vol_leader = intel.get("volatility_index", {}).get("leading_commodity", "")

    alert_comms = {
        k: v for k, v in intel.get("early_warning_alerts", {})
                              .get("per_commodity", {}).items()
        if v.get("alert_level") in ["Severe", "High"]
    }

    prompt = f"""You are a Nigerian agricultural commodity market analyst writing a daily briefing.

Market data for {run_date.strftime('%A, %d %B %Y')}:
- Food Price Index: {fpi} (base 2025=100, food costs {round(fpi-100)}% more than June 2025)
- 30-Day Outlook: {outlook:+.1f}% expected average price change
- Biggest Riser: {riser.get('commodity','N/A')} ({riser.get('day_change_pct',0):+.2f}%)
- Biggest Faller: {faller.get('commodity','N/A')} ({faller.get('day_change_pct',0):+.2f}%)
- Most Volatile: {vol_leader}
- Active Severe/High Alerts: {', '.join(f"{k} ({v['alert_level']})" for k,v in alert_comms.items()) if alert_comms else 'None'}

Write a 120-150 word daily market commentary for Nigerian food businesses and procurement teams.
Rules:
- Write like Bloomberg or Reuters, accessible to non-experts
- Lead with the most important signal, not always the same opening
- Translate numbers into what they mean for buyers/sellers
- Give one clear actionable insight
- Plain English, no jargon, no bullet points, flowing prose only
- No em dashes
- End with what to watch going forward
- Output only the commentary text, no title or labels"""

    try:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-flash-latest:generateContent?key={api_key}"
        )

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.7}
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            candidate = result["candidates"][0]
            parts = candidate.get("content", {}).get("parts", [])
            if not parts:
                raise ValueError(f"Empty Gemini response: {candidate.get('finishReason')}")
            return parts[0]["text"].strip()

    except Exception as e:
        # Fallback template
        return _template(fpi, outlook, riser, faller, alert_comms, run_date)


def _template(fpi, outlook, riser, faller, alert_comms, run_date):
    month     = run_date.strftime("%B")
    fpi_pct   = round(fpi - 100)
    direction = "rise" if outlook > 0 else "fall"
    out_abs   = abs(round(outlook, 1))
    r_name    = riser.get("commodity", "")
    r_pct     = riser.get("day_change_pct", 0)
    f_name    = faller.get("commodity", "")
    f_pct     = faller.get("day_change_pct", 0)

    severe = [k for k,v in alert_comms.items() if v.get("alert_level") == "Severe"]
    alert_txt = (f" {severe[0]} is on a Severe alert, trading well above its"
                 f" three-month average." if severe else "")

    buy_sell = ("cautious optimism for sellers but a window for buyers to stock up"
                if direction == "rise" else
                "potential relief for buyers as prices ease")

    return (
        f"Nigerian food commodity markets remain elevated in {month}, with the Food"
        f" Price Index at {fpi}, indicating food costs {fpi_pct}% more than the 2025 baseline."
        f"{alert_txt} Today {r_name} posted a gain of {r_pct:+.2f}%,"
        f" while {f_name} eased {f_pct:+.2f}%."
        f" The broader market points toward a {direction} of {out_abs}% over 30 days,"
        f" suggesting {buy_sell}."
        f" Procurement teams should monitor daily movements closely as small"
        f" price shifts compound significantly over weekly purchasing cycles."
    )


def run_commentary(intel_path: str = None) -> str:
    run_date = datetime.now()
    date_str = run_date.strftime("%Y-%m-%d")

    if intel_path and os.path.exists(intel_path):
        with open(intel_path) as f:
            intel = json.load(f)
    else:
        files = sorted(glob.glob(os.path.join(INTEL_DIR, "intelligence_*.json")))
        if not files:
            return "No intelligence data available."
        with open(files[-1]) as f:
            intel = json.load(f)

    print("  Generating AI market commentary...")
    commentary = generate_commentary(intel, run_date)

    # Build full alert text
    national = intel.get("prices_with_units", {})
    price_lines = ""
    for commodity, data in national.items():
        price = data.get("price", 0)
        chg   = data.get("day_change", 0)
        if price > 0:
            p_str = f"N{price/1e6:.3f}M" if price >= 1_000_000 else f"N{price/1000:.1f}K"
            price_lines += f"  {commodity:<22} {p_str}/MT  ({chg:+.2f}%/day)\n"

    fpi  = intel.get("food_price_index", {}).get("value", "--")
    out  = intel.get("outlook_30d", {}).get("avg_pct_change", 0)
    conf = intel.get("model_confidence", {}).get("avg_pct", "--")

    full = (
        f"AGROLINKING MARKET INTELLIGENCE\n"
        f"{run_date.strftime('%A, %d %B %Y')}\n"
        f"{'='*55}\n\n"
        f"DAILY MARKET COMMENTARY\n"
        f"{'-'*55}\n"
        f"{commentary}\n\n"
        f"{'='*55}\n"
        f"COMMODITY PRICE SUMMARY\n"
        f"{'-'*55}\n"
        f"{price_lines}"
        f"{'='*55}\n"
        f"Food Price Index: {fpi} (base 2025=100)\n"
        f"30-Day Outlook:   {out:+.1f}%\n"
        f"Model Confidence: {conf}%\n"
        f"{'='*55}\n"
        f"Source: Agrolinking Intelligence Platform\n"
        f"API: agrolinking-intelligence-production.up.railway.app\n"
    )

    # Save files
    with open(os.path.join(INTEL_DIR, f"commentary_{date_str}.txt"), "w", encoding="utf-8") as f:
        f.write(full)
    with open(os.path.join(ALERT_DIR, f"alert_daily_{date_str}.txt"), "w", encoding="utf-8") as f:
        f.write(full)

    print(f"  Commentary saved -> {INTEL_DIR}/commentary_{date_str}.txt")
    print(f"  Daily alert saved -> {ALERT_DIR}/alert_daily_{date_str}.txt")
    print(f"\n  PREVIEW:\n  {'-'*50}")
    for line in commentary.split('\n')[:4]:
        print(f"  {line}")
    print(f"  {'-'*50}")

    return commentary


if __name__ == "__main__":
    run_commentary()
