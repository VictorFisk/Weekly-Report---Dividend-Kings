"""
Dividend Kings Weekly Report Generator
=======================================
Fetches live data via yfinance (free, no API key needed) and generates
a self-contained HTML email. Run locally or via GitHub Actions.

Dependencies:  pip install yfinance jinja2
"""

import yfinance as yf
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ── STATIC METADATA (things yfinance doesn't reliably provide) ────────────────
# Dividend streak years and sector classifications are maintained manually.
# Update streak counts once per year after each company's annual raise.

KINGS_META = {
    "KO":  {"name": "Coca-Cola",            "sector": "Consumer Staples",       "sectorClass": "staples",    "streak": 62},
    "PG":  {"name": "Procter & Gamble",      "sector": "Consumer Staples",       "sectorClass": "staples",    "streak": 67},
    "JNJ": {"name": "Johnson & Johnson",     "sector": "Healthcare",             "sectorClass": "health",     "streak": 62},
    "MMM": {"name": "3M Company",            "sector": "Industrials",            "sectorClass": "industrial", "streak": 65},
    "CL":  {"name": "Colgate-Palmolive",     "sector": "Consumer Staples",       "sectorClass": "staples",    "streak": 61},
    "EMR": {"name": "Emerson Electric",      "sector": "Industrials",            "sectorClass": "industrial", "streak": 67},
    "GPC": {"name": "Genuine Parts Co.",     "sector": "Consumer Discretionary", "sectorClass": "consumer",   "streak": 67},
    "LOW": {"name": "Lowe's Companies",      "sector": "Consumer Discretionary", "sectorClass": "consumer",   "streak": 61},
    "SYY": {"name": "Sysco Corporation",     "sector": "Food Service",           "sectorClass": "staples",    "streak": 53},
    "ABT": {"name": "Abbott Laboratories",   "sector": "Healthcare",             "sectorClass": "health",     "streak": 52},
}

TICKERS = list(KINGS_META.keys())


def safe(val, fallback=0.0):
    """Return fallback if val is None, NaN, or non-numeric."""
    try:
        v = float(val)
        return fallback if (v != v) else v   # NaN check
    except (TypeError, ValueError):
        return fallback


def pct_change_cagr(series_values, years):
    """
    Compute CAGR from a list of annual dividend values.
    series_values: list of floats oldest → newest, length >= years+1
    """
    try:
        if len(series_values) < years + 1:
            return 0.0
        start = series_values[-(years + 1)]
        end   = series_values[-1]
        if start <= 0 or end <= 0:
            return 0.0
        return round(((end / start) ** (1 / years) - 1) * 100, 2)
    except Exception:
        return 0.0


def fetch_stock(ticker):
    """Fetch all required fields for one ticker. Returns a dict or None on failure."""
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info  # dict of fundamentals

        # ── Price & daily change ──────────────────────────────────────────────
        price     = safe(info.get("currentPrice") or info.get("regularMarketPrice"))
        prev_close= safe(info.get("previousClose") or info.get("regularMarketPreviousClose"))
        chg       = round(((price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0

        # ── 6-month price change ──────────────────────────────────────────────
        hist_6m = tk.history(period="6mo")
        if not hist_6m.empty:
            price_6m_ago = float(hist_6m["Close"].iloc[0])
            chg6m = round(((price - price_6m_ago) / price_6m_ago) * 100, 1)
        else:
            chg6m = 0.0

        # ── SMA 50 & 200 ─────────────────────────────────────────────────────
        hist_1y = tk.history(period="1y")
        if len(hist_1y) >= 200:
            closes  = hist_1y["Close"]
            sma50   = round(float(closes.tail(50).mean()), 2)
            sma200  = round(float(closes.tail(200).mean()), 2)
        else:
            sma50 = sma200 = price

        # ── 52-week high/low ─────────────────────────────────────────────────
        w52hi = round(safe(info.get("fiftyTwoWeekHigh")), 2)
        w52lo = round(safe(info.get("fiftyTwoWeekLow")),  2)

        # ── Dividend metrics ─────────────────────────────────────────────────
        ann_div  = round(safe(info.get("dividendRate")), 2)
        div_yield= round(safe(info.get("dividendYield")) * 100, 2)
        payout   = round(safe(info.get("payoutRatio")) * 100)

        # ── Valuation & fundamentals ─────────────────────────────────────────
        pe       = round(safe(info.get("trailingPE")),  1)
        ps       = round(safe(info.get("priceToSalesTrailing12Months")), 1)
        eps      = round(safe(info.get("trailingEps")), 2)
        beta     = round(safe(info.get("beta"), 1.0),  2)
        mktcap   = round(safe(info.get("marketCap")) / 1e9, 1)

        # ── Dividend CAGR (5Y and 10Y) from dividend history ─────────────────
        div_hist = tk.dividends
        if not div_hist.empty:
            # Resample to annual totals
            annual = div_hist.resample("YE").sum()
            annual_vals = [float(v) for v in annual.values if v > 0]
        else:
            annual_vals = []

        cagr5  = pct_change_cagr(annual_vals, 5)
        cagr10 = pct_change_cagr(annual_vals, 10)

        # ── EPS growth (3Y) using earningsHistory or fallback ────────────────
        try:
            earnings = tk.earnings_history
            if earnings is not None and not earnings.empty and len(earnings) >= 4:
                eps_vals = [safe(e) for e in earnings["epsActual"].dropna().tail(9).values]
                # annualise: take end-of-year sums (4 quarters each)
                eps_annual = [sum(eps_vals[i:i+4]) for i in range(0, len(eps_vals)-3, 4)]
                eps_growth = pct_change_cagr(eps_annual, min(3, len(eps_annual)-1))
            else:
                eps_growth = round(safe(info.get("earningsGrowth")) * 100, 1)
        except Exception:
            eps_growth = round(safe(info.get("earningsGrowth")) * 100, 1)

        # ── Revenue growth (3Y) ───────────────────────────────────────────────
        try:
            financials = tk.financials  # annual income statement, newest first
            if financials is not None and not financials.empty:
                rev_row = financials.loc["Total Revenue"] if "Total Revenue" in financials.index else None
                if rev_row is not None and len(rev_row) >= 4:
                    rev_vals = [safe(v) for v in reversed(rev_row.values[:4])]
                    rev_growth = pct_change_cagr(rev_vals, 3)
                else:
                    rev_growth = round(safe(info.get("revenueGrowth")) * 100, 1)
            else:
                rev_growth = round(safe(info.get("revenueGrowth")) * 100, 1)
        except Exception:
            rev_growth = round(safe(info.get("revenueGrowth")) * 100, 1)

        return {
            "ticker":    ticker,
            "price":     round(price, 2),
            "chg":       chg,
            "chg6m":     chg6m,
            "yield":     div_yield,
            "payout":    payout,
            "annDiv":    ann_div,
            "pe":        pe,
            "ps":        ps,
            "eps":       eps,
            "beta":      beta,
            "mktcap":    mktcap,
            "sma50":     sma50,
            "sma200":    sma200,
            "w52lo":     w52lo,
            "w52hi":     w52hi,
            "cagr5":     cagr5,
            "cagr10":    cagr10,
            "epsGrowth": round(eps_growth, 1),
            "revGrowth": round(rev_growth, 1),
        }

    except Exception as e:
        print(f"  ⚠️  Failed to fetch {ticker}: {e}", file=sys.stderr)
        return None


def compute_best_value(stocks):
    """
    Score each stock 0-100 using the same 6-criterion model as the HTML report.
    Returns the stocks list sorted best → worst, each with .composite and .breakdown.
    """

    def sma_score(d):
        p, s50, s200 = d["price"], d["sma50"], d["sma200"]
        gap = ((s50 - s200) / s200 * 100) if s200 else 0
        if p > s50 and s50 > s200:        return 10  # Bullish
        if p < s50 and s50 < s200:        return 1   # Bearish
        if s50 > s200 and gap < 2:        return 8   # Golden Cross
        if s50 < s200 and gap > -2:       return 2   # Death Cross
        return 5                                      # Neutral

    criteria = [
        {"key": "yield",   "label": "Dividend Yield",       "weight": 18,
         "score": lambda d: min(10, (d["yield"] / 6) * 10)},
        {"key": "pe",      "label": "P/E Valuation",        "weight": 20,
         "score": lambda d: min(10, max(0, (40 - d["pe"]) / 3))},
        {"key": "payout",  "label": "Payout Safety",        "weight": 15,
         "score": lambda d: min(10, max(0, (85 - d["payout"]) / 7.5))},
        {"key": "growth",  "label": "Growth Factor",        "weight": 25,
         "score": lambda d: (
             min(10, max(0, (d["epsGrowth"] + 2) / 1.7)) * 0.4 +
             min(10, max(0, (d["revGrowth"] + 1) / 1.5)) * 0.3 +
             min(10, max(0, 5 + (d["cagr5"] - d["cagr10"]) * 0.8)) * 0.3
         )},
        {"key": "sma",     "label": "SMA Trend Signal",     "weight": 12,
         "score": sma_score},
        {"key": "beta",    "label": "Low Volatility (Beta)", "weight": 10,
         "score": lambda d: min(10, max(0, (1.2 - d["beta"]) / 0.12))},
    ]

    max_possible = sum(c["weight"] * 10 for c in criteria)
    results = []
    for s in stocks:
        total = 0
        breakdown = []
        for c in criteria:
            raw = c["score"](s)
            total += raw * c["weight"]
            breakdown.append({"label": c["label"], "weight": c["weight"], "score": round(raw, 1)})
        composite = round((total / max_possible) * 100)
        results.append({**s, "composite": composite, "breakdown": breakdown})

    return sorted(results, key=lambda x: x["composite"], reverse=True)


def sma_label(d):
    p, s50, s200 = d["price"], d["sma50"], d["sma200"]
    if s200 == 0:
        return "🟡 Neutral"
    gap = ((s50 - s200) / s200 * 100)
    if p > s50 and s50 > s200:        return "🟢 Bullish"
    if p < s50 and s50 < s200:        return "🔴 Bearish"
    if s50 > s200 and gap < 2:        return "✨ Golden Cross"
    if s50 < s200 and gap > -2:       return "💀 Death Cross"
    return "🟡 Neutral"


def fmt_sign(val):
    return f"+{val:.1f}" if val >= 0 else f"{val:.1f}"


def generate_html(scored_stocks, run_date):
    """Build the complete email HTML with live data injected."""

    winner      = scored_stocks[0]
    top5        = scored_stocks[:5]
    all_stocks  = scored_stocks  # sorted best→worst for leaderboard

    # Re-sort the display table alphabetically by ticker for readability
    display_stocks = sorted(scored_stocks, key=lambda d: d["ticker"])

    ups   = sum(1 for s in display_stocks if s["chg"] >= 0)
    downs = len(display_stocks) - ups
    avg_yield = round(sum(s["yield"] for s in display_stocks) / len(display_stocks), 2)
    avg_pe    = round(sum(s["pe"]    for s in display_stocks) / len(display_stocks), 1)
    avg_cagr  = round(sum(s["cagr10"] for s in display_stocks) / len(display_stocks), 1)

    # ── Helper: render one table row ─────────────────────────────────────────
    def table_row(s, idx):
        is_winner = s["ticker"] == winner["ticker"]
        bg = "background:rgba(74,222,128,0.05);" if is_winner else ("background:rgba(255,255,255,0.02);" if idx % 2 == 0 else "")
        ticker_color = "#4ade80" if is_winner else "#60a5fa"
        star = " ★" if is_winner else ""

        chg_color  = "#22c55e" if s["chg"]  >= 0 else "#ef4444"
        chg6m_color= "#22c55e" if s["chg6m"]>= 0 else "#ef4444"
        chg_arrow  = "▲" if s["chg"]  >= 0 else "▼"
        chg6m_arrow= "▲" if s["chg6m"]>= 0 else "▼"

        yield_html = (f'<span style="background:rgba(240,165,0,0.2);color:#f0a500;font-size:10px;font-weight:700;padding:2px 5px;border-radius:4px;">★ {s["yield"]:.2f}%</span>'
                      if s["yield"] >= 3 else
                      f'<span style="font-size:10px;font-weight:700;color:#8ab4d4;">{s["yield"]:.2f}%</span>')

        payout_color = "#22c55e" if s["payout"] < 60 else ("#ef4444" if s["payout"] > 80 else "#eab308")
        pe_color     = "#4ade80" if s["pe"] < 20 else ("#f87171" if s["pe"] > 30 else "#e2e8f0")

        sma = sma_label(s)
        if "Bullish" in sma:
            sma_bg, sma_col = "rgba(16,185,129,0.15)", "#34d399"
            sma_short = "🟢 Bull"
        elif "Bearish" in sma:
            sma_bg, sma_col = "rgba(239,68,68,0.15)", "#f87171"
            sma_short = "🔴 Bear"
        elif "Golden" in sma:
            sma_bg, sma_col = "rgba(234,179,8,0.15)", "#fbbf24"
            sma_short = "✨ Cross+"
        elif "Death" in sma:
            sma_bg, sma_col = "rgba(239,68,68,0.1)", "#f87171"
            sma_short = "💀 Cross-"
        else:
            sma_bg, sma_col = "rgba(234,179,8,0.12)", "#eab308"
            sma_short = "🟡 Neut"

        meta = KINGS_META[s["ticker"]]

        return f"""
                <tr style="border-bottom:1px solid #1a2e42;{bg}">
                  <td style="padding:10px 7px 10px 0;font-family:monospace;font-size:12px;font-weight:700;color:{ticker_color};">{s["ticker"]}{star}</td>
                  <td style="padding:10px 7px;font-size:11px;color:#e2e8f0;">{meta["name"]}</td>
                  <td style="padding:10px 7px;text-align:right;">
                    <div style="font-size:11px;font-weight:600;color:#e2e8f0;">${s["price"]:.2f}</div>
                    <div style="font-size:10px;color:{chg_color};font-weight:600;">{chg_arrow} {abs(s["chg"]):.2f}%</div>
                  </td>
                  <td style="padding:10px 7px;text-align:right;font-size:10px;font-weight:700;color:{chg6m_color};">{chg6m_arrow} {abs(s["chg6m"]):.1f}%</td>
                  <td style="padding:10px 7px;text-align:right;">{yield_html}</td>
                  <td style="padding:10px 7px;text-align:right;"><span style="color:{payout_color};font-size:10px;font-weight:700;">● {s["payout"]}%</span></td>
                  <td style="padding:10px 7px;text-align:right;font-size:10px;color:{pe_color};font-weight:700;">{s["pe"]:.1f}×</td>
                  <td style="padding:10px 7px;text-align:right;"><span style="background:#1e3a52;color:#93c5fd;font-size:9px;font-weight:700;padding:2px 5px;border-radius:20px;white-space:nowrap;">{meta["streak"]} yrs</span></td>
                  <td style="padding:10px 0 10px 7px;text-align:right;"><span style="background:{sma_bg};color:{sma_col};font-size:9px;font-weight:700;padding:2px 5px;border-radius:4px;">{sma_short}</span></td>
                </tr>"""

    # ── Leaderboard mini tiles ────────────────────────────────────────────────
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    medal_colors = ["#4ade80", "#f0a500", "#60a5fa", "#8ab4d4", "#8ab4d4"]

    leaderboard_cells = ""
    for i, s in enumerate(top5):
        border = "border:1px solid rgba(74,222,128,0.35);" if i == 0 else "border:1px solid #1e3a52;"
        bg_tile = "background:rgba(74,222,128,0.1);" if i == 0 else "background:#0f1c2e;"
        leaderboard_cells += f"""
                      <td style="width:20%;{'padding-right:5px;' if i<4 else ''}text-align:center;">
                        <div style="{bg_tile}{border}border-radius:6px;padding:6px 4px;">
                          <div style="font-size:9px;font-weight:700;color:{medal_colors[i]};font-family:monospace;">{medals[i]} {s["ticker"]}</div>
                          <div style="font-size:12px;font-weight:700;color:{medal_colors[i]};">{s["composite"]}</div>
                        </div>
                      </td>"""

    # ── Top gainer / highest yield / longest streak ──────────────────────────
    top_gainer  = max(display_stocks, key=lambda s: s["chg6m"])
    top_yield   = max(display_stocks, key=lambda s: s["yield"])
    top_streak  = max(display_stocks, key=lambda s: KINGS_META[s["ticker"]]["streak"])

    # ── Assemble full HTML ────────────────────────────────────────────────────
    table_rows_html = "\n".join(table_row(s, i) for i, s in enumerate(display_stocks))
    winner_meta = KINGS_META[winner["ticker"]]
    # Top 3 scoring criteria pills for winner
    top3 = sorted(winner["breakdown"], key=lambda c: c["score"], reverse=True)[:3]
    pills_html = "".join(
        f'<td><span style="background:rgba(74,222,128,0.12);color:#4ade80;border:1px solid rgba(74,222,128,0.3);font-size:10px;font-weight:700;padding:3px 8px;border-radius:20px;white-space:nowrap;">{c["label"]}: {c["score"]:.1f}/10</span></td>'
        for c in top3
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>American Dividend Kings — Weekly Snapshot</title>
</head>
<body style="margin:0;padding:0;background-color:#0f1923;font-family:'Georgia',serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#0f1923;">
  <tr>
    <td align="center" style="padding:24px 12px;">
      <table width="650" cellpadding="0" cellspacing="0" style="max-width:650px;width:100%;background-color:#131f2e;border-radius:12px;overflow:hidden;box-shadow:0 8px 40px rgba(0,0,0,0.5);">

        <!-- HEADER -->
        <tr>
          <td style="background:linear-gradient(135deg,#1a3a5c 0%,#0d2137 50%,#1a2c1a 100%);padding:36px 32px 28px;border-bottom:3px solid #f0a500;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <div style="font-size:11px;letter-spacing:3px;color:#f0a500;text-transform:uppercase;margin-bottom:8px;">📊 Market Intelligence</div>
                  <div style="font-size:28px;font-weight:700;color:#ffffff;line-height:1.2;">🏆 American Dividend Kings</div>
                  <div style="font-size:13px;color:#8ab4d4;margin-top:6px;letter-spacing:1px;text-transform:uppercase;">Weekly Snapshot Report</div>
                </td>
                <td align="right" style="vertical-align:top;">
                  <div style="background:rgba(240,165,0,0.15);border:1px solid #f0a500;border-radius:8px;padding:10px 16px;text-align:center;">
                    <div style="font-size:10px;color:#f0a500;letter-spacing:2px;text-transform:uppercase;">Report Date</div>
                    <div style="font-size:14px;color:#ffffff;font-weight:600;margin-top:4px;">{run_date}</div>
                    <div style="font-size:10px;color:#4ade80;margin-top:3px;">● Live Data</div>
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- STATS BAR -->
        <tr>
          <td style="padding:18px 32px;background:#0d1a26;border-bottom:1px solid #1e3a52;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="text-align:center;padding:0 8px;border-right:1px solid #1e3a52;">
                  <div style="font-size:22px;font-weight:700;color:#f0a500;font-family:Georgia,serif;">10</div>
                  <div style="font-size:9px;color:#8ab4d4;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Kings Tracked</div>
                </td>
                <td style="text-align:center;padding:0 8px;border-right:1px solid #1e3a52;">
                  <div style="font-size:22px;font-weight:700;color:#22c55e;font-family:Georgia,serif;">{ups}</div>
                  <div style="font-size:9px;color:#8ab4d4;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Up Today</div>
                </td>
                <td style="text-align:center;padding:0 8px;border-right:1px solid #1e3a52;">
                  <div style="font-size:22px;font-weight:700;color:#ef4444;font-family:Georgia,serif;">{downs}</div>
                  <div style="font-size:9px;color:#8ab4d4;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Down Today</div>
                </td>
                <td style="text-align:center;padding:0 8px;border-right:1px solid #1e3a52;">
                  <div style="font-size:22px;font-weight:700;color:#60a5fa;font-family:Georgia,serif;">{avg_yield}%</div>
                  <div style="font-size:9px;color:#8ab4d4;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Avg. Yield</div>
                </td>
                <td style="text-align:center;padding:0 8px;border-right:1px solid #1e3a52;">
                  <div style="font-size:22px;font-weight:700;color:#c084fc;font-family:Georgia,serif;">{avg_cagr}%</div>
                  <div style="font-size:9px;color:#8ab4d4;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Avg 10Y CAGR</div>
                </td>
                <td style="text-align:center;padding:0 8px;">
                  <div style="font-size:22px;font-weight:700;color:#f87171;font-family:Georgia,serif;">{avg_pe}×</div>
                  <div style="font-size:9px;color:#8ab4d4;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Avg P/E</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- BEST VALUE BANNER -->
        <tr>
          <td style="padding:20px 32px 0;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#0d2410,#0f1c2e,#1a1a0a);border:2px solid #4ade80;border-radius:12px;overflow:hidden;">
              <tr>
                <td style="padding:18px 20px;vertical-align:middle;">
                  <div style="font-size:9px;letter-spacing:3px;color:#4ade80;text-transform:uppercase;font-weight:700;margin-bottom:6px;">⭐ Best Value Pick · Composite Score</div>
                  <div style="font-size:22px;font-weight:700;color:#ffffff;line-height:1.1;font-family:Georgia,serif;">{winner_meta["name"]}</div>
                  <div style="font-size:11px;color:#4ade80;font-family:monospace;font-weight:700;margin-top:4px;letter-spacing:1px;">{winner["ticker"]} · ${winner["price"]:.2f} · {winner_meta["sector"]}</div>
                  <table cellpadding="0" cellspacing="4" style="margin-top:10px;">
                    <tr>{pills_html}</tr>
                  </table>
                  <div style="margin-top:12px;font-size:11px;color:#8ab4d4;line-height:1.65;border-top:1px solid rgba(74,222,128,0.15);padding-top:10px;">
                    Scores highest across 6 weighted criteria — valuation, income, payout safety, growth, SMA trend, and volatility.
                    P/E: <strong style="color:#4ade80;">{winner["pe"]:.1f}×</strong> ·
                    Yield: <strong style="color:#4ade80;">{winner["yield"]:.2f}%</strong> ·
                    Payout: <strong style="color:#4ade80;">{winner["payout"]}%</strong> ·
                    EPS growth: <strong style="color:#4ade80;">{fmt_sign(winner["epsGrowth"])}%</strong>
                  </div>
                </td>
                <td style="padding:18px 20px 18px 0;vertical-align:middle;text-align:center;width:76px;">
                  <table cellpadding="0" cellspacing="0" align="center">
                    <tr>
                      <td style="width:72px;height:72px;border-radius:50%;border:3px solid #4ade80;background:rgba(74,222,128,0.08);text-align:center;vertical-align:middle;">
                        <div style="font-size:26px;font-weight:700;color:#4ade80;font-family:Georgia,serif;line-height:1.1;">{winner["composite"]}</div>
                        <div style="font-size:8px;color:#4ade80;letter-spacing:1px;text-transform:uppercase;opacity:0.7;">Score</div>
                      </td>
                    </tr>
                  </table>
                  <div style="font-size:9px;color:rgba(74,222,128,0.45);margin-top:5px;letter-spacing:1px;text-transform:uppercase;">of 100</div>
                </td>
              </tr>
              <tr>
                <td colspan="2" style="padding:0 20px 16px;">
                  <div style="font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;font-weight:700;margin-bottom:8px;">Value Leaderboard (top 5)</div>
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>{leaderboard_cells}</tr>
                  </table>
                  <div style="font-size:9px;color:rgba(74,222,128,0.35);margin-top:7px;text-align:right;">Weights: P/E 20% · Growth 25% · Yield 18% · Payout 15% · SMA 12% · Beta 10%</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- SECTION LABEL -->
        <tr>
          <td style="padding:22px 32px 10px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="font-size:10px;letter-spacing:3px;color:#f0a500;text-transform:uppercase;font-weight:700;">📋 Full Kings Snapshot</td>
                <td align="right" style="font-size:10px;color:#4a6a8a;">All 10 companies · {run_date} · Live Data</td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- LEGEND -->
        <tr>
          <td style="padding:8px 32px;background:#0d1a26;border-top:1px solid #1e3a52;border-bottom:1px solid #1e3a52;">
            <table cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding-right:14px;font-size:10px;color:#8ab4d4;white-space:nowrap;"><span style="color:#22c55e;font-weight:700;">▲</span> Up <span style="color:#ef4444;font-weight:700;">▼</span> Down</td>
                <td style="padding-right:14px;font-size:10px;color:#8ab4d4;white-space:nowrap;"><span style="color:#f0a500;font-weight:700;">★</span> Yield &gt;3%</td>
                <td style="padding-right:14px;font-size:10px;color:#8ab4d4;white-space:nowrap;">Payout: <span style="color:#22c55e;">●</span>&lt;60% <span style="color:#eab308;">●</span>60–80% <span style="color:#ef4444;">●</span>&gt;80%</td>
                <td style="font-size:10px;color:#4ade80;white-space:nowrap;font-weight:700;">★ = Best Value</td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- DATA TABLE -->
        <tr>
          <td style="padding:0 32px;">
            <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
              <thead>
                <tr style="background:#0d1a26;">
                  <th style="padding:10px 7px 10px 0;text-align:left;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;">Ticker</th>
                  <th style="padding:10px 7px;text-align:left;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;">Company</th>
                  <th style="padding:10px 7px;text-align:right;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;white-space:nowrap;">Price / Chg</th>
                  <th style="padding:10px 7px;text-align:right;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;white-space:nowrap;">6M</th>
                  <th style="padding:10px 7px;text-align:right;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;white-space:nowrap;">Yield</th>
                  <th style="padding:10px 7px;text-align:right;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;white-space:nowrap;">Pay.</th>
                  <th style="padding:10px 7px;text-align:right;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;white-space:nowrap;">P/E</th>
                  <th style="padding:10px 7px;text-align:right;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;white-space:nowrap;">Streak</th>
                  <th style="padding:10px 0 10px 7px;text-align:right;font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;border-bottom:2px solid #1e3a52;white-space:nowrap;">SMA</th>
                </tr>
              </thead>
              <tbody>
                {table_rows_html}
              </tbody>
            </table>
          </td>
        </tr>

        <!-- HIGHLIGHTS -->
        <tr>
          <td style="padding:20px 32px 0;">
            <div style="font-size:10px;letter-spacing:3px;color:#f0a500;text-transform:uppercase;font-weight:700;margin-bottom:12px;">🔦 This Week's Highlights</div>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="width:33%;padding-right:8px;vertical-align:top;">
                  <div style="background:#0d1a26;border:1px solid #1e3a52;border-top:3px solid #22c55e;border-radius:8px;padding:12px;">
                    <div style="font-size:9px;letter-spacing:2px;color:#22c55e;text-transform:uppercase;font-weight:700;margin-bottom:6px;">📈 Top 6M Gainer</div>
                    <div style="font-size:14px;font-weight:700;color:#22c55e;font-family:monospace;">{top_gainer["ticker"]}</div>
                    <div style="font-size:10px;color:#e2e8f0;margin-top:2px;">{KINGS_META[top_gainer["ticker"]]["name"]}</div>
                    <div style="font-size:14px;font-weight:700;color:#22c55e;margin-top:6px;">▲ +{top_gainer["chg6m"]:.1f}%</div>
                    <div style="font-size:9px;color:#8ab4d4;margin-top:2px;">6-month change</div>
                  </div>
                </td>
                <td style="width:33%;padding-right:8px;vertical-align:top;">
                  <div style="background:#0d1a26;border:1px solid #1e3a52;border-top:3px solid #f0a500;border-radius:8px;padding:12px;">
                    <div style="font-size:9px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;font-weight:700;margin-bottom:6px;">💰 Highest Yield</div>
                    <div style="font-size:14px;font-weight:700;color:#f0a500;font-family:monospace;">{top_yield["ticker"]}</div>
                    <div style="font-size:10px;color:#e2e8f0;margin-top:2px;">{KINGS_META[top_yield["ticker"]]["name"]}</div>
                    <div style="font-size:14px;font-weight:700;color:#f0a500;margin-top:6px;">★ {top_yield["yield"]:.2f}%</div>
                    <div style="font-size:9px;color:#8ab4d4;margin-top:2px;">Annual yield</div>
                  </div>
                </td>
                <td style="width:33%;vertical-align:top;">
                  <div style="background:#0d1a26;border:1px solid #1e3a52;border-top:3px solid #60a5fa;border-radius:8px;padding:12px;">
                    <div style="font-size:9px;letter-spacing:2px;color:#60a5fa;text-transform:uppercase;font-weight:700;margin-bottom:6px;">👑 Longest Streak</div>
                    <div style="font-size:14px;font-weight:700;color:#60a5fa;font-family:monospace;">{top_streak["ticker"]}</div>
                    <div style="font-size:10px;color:#e2e8f0;margin-top:2px;">{KINGS_META[top_streak["ticker"]]["name"]}</div>
                    <div style="font-size:14px;font-weight:700;color:#60a5fa;margin-top:6px;">{KINGS_META[top_streak["ticker"]]["streak"]} yrs</div>
                    <div style="font-size:9px;color:#8ab4d4;margin-top:2px;">Consecutive raises</div>
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- GLOSSARY -->
        <tr>
          <td style="padding:20px 32px;background:#0d1a26;border-top:1px solid #1e3a52;">
            <div style="font-size:10px;letter-spacing:2px;color:#f0a500;text-transform:uppercase;margin-bottom:12px;font-weight:700;">📖 Quick Reference</div>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding-bottom:8px;vertical-align:top;width:50%;">
                  <div style="font-size:10px;color:#60a5fa;font-weight:700;margin-bottom:2px;">Yield ★</div>
                  <div style="font-size:10px;color:#8ab4d4;line-height:1.5;">Annual dividend ÷ price. ★ marks yields above 3%.</div>
                </td>
                <td style="padding-bottom:8px;vertical-align:top;padding-left:20px;width:50%;">
                  <div style="font-size:10px;color:#60a5fa;font-weight:700;margin-bottom:2px;">Payout Ratio ●</div>
                  <div style="font-size:10px;color:#8ab4d4;line-height:1.5;"><span style="color:#22c55e;">Green &lt;60%</span> safe · <span style="color:#eab308;">Amber 60–80%</span> watch · <span style="color:#ef4444;">Red &gt;80%</span> risky.</div>
                </td>
              </tr>
              <tr>
                <td style="vertical-align:top;">
                  <div style="font-size:10px;color:#4ade80;font-weight:700;margin-bottom:2px;">Best Value ★</div>
                  <div style="font-size:10px;color:#8ab4d4;line-height:1.5;">Top composite score: P/E 20%, Growth 25%, Yield 18%, Payout 15%, SMA 12%, Beta 10%.</div>
                </td>
                <td style="vertical-align:top;padding-left:20px;">
                  <div style="font-size:10px;color:#60a5fa;font-weight:700;margin-bottom:2px;">SMA Signal</div>
                  <div style="font-size:10px;color:#8ab4d4;line-height:1.5;"><span style="color:#34d399;">🟢 Bull</span> uptrend · <span style="color:#f87171;">🔴 Bear</span> downtrend · <span style="color:#eab308;">🟡 Neut</span> mixed.</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- CTA -->
        <tr>
          <td style="padding:20px 32px;text-align:center;border-top:1px solid #1e3a52;">
            <div style="font-size:10px;color:#4a6a8a;margin-top:8px;">Data fetched via Yahoo Finance · Generated {run_date}</div>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="padding:16px 32px;background:#0a1520;border-top:1px solid #1e3a52;">
            <p style="margin:0;font-size:10px;color:#4a6a8a;line-height:1.6;text-align:center;">
              ⚠️ <strong style="color:#6a8aaa;">Disclaimer:</strong> For informational purposes only. Data sourced from Yahoo Finance and may be delayed or inaccurate. Past dividend performance does not guarantee future results. Not financial advice.<br>
              <span style="color:#3a5a7a;">Auto-generated by GitHub Actions · © Dividend Kings Report</span>
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""

    return html


def main():
    print("📡 Fetching live data from Yahoo Finance...")
    stocks = []
    for ticker in TICKERS:
        print(f"  → {ticker}...", end=" ", flush=True)
        result = fetch_stock(ticker)
        if result:
            stocks.append(result)
            print("✓")
        else:
            print("✗ skipped")

    if not stocks:
        print("❌ No data fetched. Aborting.", file=sys.stderr)
        sys.exit(1)

    print(f"\n🧮 Scoring {len(stocks)} stocks...")
    scored = compute_best_value(stocks)

    print(f"🏅 Best Value: {scored[0]['ticker']} (score: {scored[0]['composite']})")

    run_date = datetime.now().strftime("%B %-d, %Y")
    html = generate_html(scored, run_date)

    out_path = Path("dividend_kings_email_live.html")
    out_path.write_text(html, encoding="utf-8")
    print(f"\n✅ Report saved → {out_path}")


if __name__ == "__main__":
    main()
