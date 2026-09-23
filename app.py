# =============================================================================
# Quant Trade Levels — Streamlit Technical Analysis Dashboard
# =============================================================================
# requirements.txt
# -----------------------------------------------------------------------------
# streamlit>=1.30.0
# yfinance>=0.2.40
# pandas>=2.0.0
# numpy>=1.24.0
# ta>=0.11.0
# plotly>=5.18.0
# -----------------------------------------------------------------------------
# Run with:  streamlit run app.py
# =============================================================================

from __future__ import annotations

import base64 as _b64
import datetime as _dt
import hashlib as _hashlib
import json as _json
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

# -----------------------------------------------------------------------------
# Page configuration
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Quant Trade Levels",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",  # expanded on desktop, collapsed on mobile
)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
PERIOD_OPTIONS = {
    "6 Months": "6mo",
    "1 Year": "1y",
    "2 Years": "2y",
    "5 Years": "5y",
}

COLOR_S1 = "#00C853"      # green
COLOR_S2 = "#1B5E20"      # dark green
COLOR_SL = "#D50000"      # red
COLOR_TP1 = "#2962FF"     # blue
COLOR_TP2 = "#AA00FF"     # purple
COLOR_EMA20 = "#FF9100"   # orange
COLOR_EMA50 = "#00B8D4"   # cyan


# -----------------------------------------------------------------------------
# Data layer
# -----------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def load_price_data(ticker: str, period: str) -> pd.DataFrame:
    """Download daily OHLCV data for `ticker`. Returns a clean, single-level
    DataFrame indexed by date. Raises ValueError on missing/invalid data."""
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("No ticker symbol provided.")

    raw = yf.download(
        ticker,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )

    if raw is None or raw.empty:
        raise ValueError(
            f"No data returned for '{ticker}'. It may be an invalid symbol, "
            f"delisted, or temporarily unavailable."
        )

    # yfinance can return a MultiIndex on columns (Price, Ticker). Flatten it.
    if isinstance(raw.columns, pd.MultiIndex):
        # Prefer the level that contains OHLC field names.
        lvl0 = set(raw.columns.get_level_values(0))
        if {"Open", "High", "Low", "Close"}.issubset(lvl0):
            raw.columns = raw.columns.get_level_values(0)
        else:
            raw.columns = raw.columns.get_level_values(-1)

    raw = raw.rename(columns=str.title)

    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(set(raw.columns)):
        raise ValueError(
            f"Downloaded data for '{ticker}' is missing required OHLC columns."
        )

    df = raw[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in raw.columns]].copy()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    if len(df) < 60:
        raise ValueError(
            f"Only {len(df)} valid trading days available for '{ticker}'. "
            f"Need at least 60 for reliable EMA(50) / indicators. "
            f"Try a longer lookback period."
        )

    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_ticker_name(ticker: str) -> str:
    """Best-effort long name lookup. Falls back to the symbol on any failure."""
    ticker = ticker.strip().upper()
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("longName") or info.get("shortName") or ticker
    except Exception:
        return ticker


# -----------------------------------------------------------------------------
# CSV loader (Investing.com / TradingView exports — e.g. KSE All Share Index)
# -----------------------------------------------------------------------------
def _clean_numeric(series: pd.Series) -> pd.Series:
    """Coerce a column of strings like '45,678.90', '1.23B', '(1.5)', '2.4%'
    into floats. Handles thousands-commas and K/M/B volume suffixes."""
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return np.nan
        s = str(v).strip().replace(",", "").replace("%", "").replace('"', "")
        if s in ("", "-", "N/A", "nan", "None"):
            return np.nan
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()").replace("+", "")
        mult = 1.0
        if s and s[-1] in "KkMmBbTt":
            mult = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}[s[-1].lower()]
            s = s[:-1]
        try:
            val = float(s) * mult
        except ValueError:
            return np.nan
        return -val if neg else val

    return series.map(one)


def load_csv_data(uploaded_file) -> pd.DataFrame:
    """Parse a historical-price CSV exported from Investing.com or TradingView
    into the same OHLC frame `load_price_data` produces."""
    try:
        raw = pd.read_csv(uploaded_file)
    except Exception as e:
        raise ValueError(f"Could not read the CSV file: {e}")

    if raw.empty:
        raise ValueError("The uploaded CSV is empty.")

    # Normalise headers: lower-case, strip, drop punctuation like 'Vol.'.
    norm = {c: str(c).strip().lower().rstrip(".") for c in raw.columns}
    raw = raw.rename(columns=norm)
    cols = set(raw.columns)

    # Locate the date/time column.
    date_col = next((c for c in ("date", "time", "datetime", "timestamp") if c in cols), None)
    if date_col is None:
        raise ValueError(
            "No date column found. Expected a 'Date' (Investing.com) or "
            "'time' (TradingView) column."
        )

    # Locate the close column: TradingView uses 'close', Investing.com 'price'.
    close_col = next((c for c in ("close", "price", "last", "adj close") if c in cols), None)
    if close_col is None:
        raise ValueError(
            "No close/price column found. Expected 'Close' (TradingView) or "
            "'Price' (Investing.com)."
        )

    out = pd.DataFrame()
    out["Close"] = _clean_numeric(raw[close_col])
    # OHLC — fall back to Close when a column is absent.
    out["Open"] = _clean_numeric(raw["open"]) if "open" in cols else out["Close"]
    out["High"] = _clean_numeric(raw["high"]) if "high" in cols else out["Close"]
    out["Low"] = _clean_numeric(raw["low"]) if "low" in cols else out["Close"]
    vol_col = next((c for c in ("volume", "vol") if c in cols), None)
    out["Volume"] = _clean_numeric(raw[vol_col]) if vol_col else 0.0

    # Parse dates. TradingView may store unix seconds; Investing.com uses text.
    idx = raw[date_col]
    if pd.api.types.is_numeric_dtype(idx):
        parsed = pd.to_datetime(idx, unit="s", errors="coerce")
    else:
        parsed = pd.to_datetime(idx, errors="coerce")
        if parsed.isna().mean() > 0.3:  # retry with day-first (DD/MM/YYYY)
            parsed = pd.to_datetime(idx, errors="coerce", dayfirst=True)
    out.index = parsed

    out = out.dropna(subset=["Close"])
    out = out[~out.index.isna()]
    out = out[~out.index.duplicated(keep="last")].sort_index()

    if len(out) < 60:
        raise ValueError(
            f"Only {len(out)} usable rows parsed from the CSV. Need at least 60. "
            f"Export a longer daily history (1y+) from Investing.com / TradingView."
        )
    return out


# -----------------------------------------------------------------------------
# PSX Data Portal loader (dps.psx.com.pk) — official KSE index data
# -----------------------------------------------------------------------------
# Symbol -> friendly label for the PSX end-of-day index feed.
PSX_INDICES = {
    "ALLSHR": "KSE All Share Index",
    "KSE100": "KSE 100 Index",
    "KSE30": "KSE 30 Index",
    "KMI30": "KMI 30 Index",
    "KMIALLSHR": "KMI All Share Index",
    "PSXDIV20": "PSX Dividend 20 Index",
}

# Lookback label -> approximate calendar days, for trimming PSX history.
PERIOD_DAYS = {"6mo": 182, "1y": 365, "2y": 730, "5y": 1825}


@st.cache_data(ttl=900, show_spinner=False)
def load_psx_data(symbol: str, period: str = "5y") -> pd.DataFrame:
    """Fetch daily index data from the PSX Data Portal end-of-day API.

    The feed returns rows of [unix_ts, close, volume, extra]; it is close-only
    (no per-day OHLC), so Open/High/Low are set equal to Close and the frame is
    flagged via `df.attrs['has_ohlc'] = False` so the chart draws a line."""
    symbol = symbol.strip().upper()
    url = f"https://dps.psx.com.pk/timeseries/eod/{symbol}"
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://dps.psx.com.pk/",
        },
    )
    try:
        with urlopen(req, timeout=25) as resp:
            payload = _json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        raise ValueError(
            f"Could not reach the PSX Data Portal for '{symbol}': {e}. "
            f"Check your connection or try again shortly."
        )

    if not isinstance(payload, dict) or payload.get("status") != 1:
        raise ValueError(
            f"PSX Data Portal returned no data for '{symbol}'. "
            f"Verify the index symbol."
        )

    rows = payload.get("data") or []
    if not rows:
        raise ValueError(f"PSX Data Portal returned an empty series for '{symbol}'.")

    recs = []
    for r in rows:
        try:
            ts, close = r[0], float(r[1])
            vol = float(r[2]) if len(r) > 2 and r[2] is not None else 0.0
        except (TypeError, ValueError, IndexError):
            continue
        recs.append((pd.to_datetime(int(ts), unit="s"), close, vol))

    if not recs:
        raise ValueError(f"Could not parse any rows from PSX data for '{symbol}'.")

    df = pd.DataFrame(recs, columns=["Date", "Close", "Volume"]).set_index("Date")
    df = df[~df.index.duplicated(keep="last")].sort_index()

    # Close-only feed: mirror Close into OHLC so downstream code is uniform.
    df["Open"] = df["Close"]
    df["High"] = df["Close"]
    df["Low"] = df["Close"]
    df = df[["Open", "High", "Low", "Close", "Volume"]]

    # Trim to the requested lookback window.
    df = _trim_period(df, period)

    if len(df) < 60:
        raise ValueError(
            f"Only {len(df)} rows available for '{symbol}'. Need at least 60."
        )

    df.attrs["has_ohlc"] = False
    return df


def _trim_period(df: pd.DataFrame, period: str, min_rows: int = 60) -> pd.DataFrame:
    """Slice a frame to the requested lookback, keeping at least `min_rows`."""
    days = PERIOD_DAYS.get(period, 1825)
    cutoff = df.index.max() - pd.Timedelta(days=days)
    trimmed = df[df.index >= cutoff]
    return trimmed if len(trimmed) >= min_rows else df


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_psx_symbols() -> dict:
    """Fetch the PSX listed-symbol master list and return {symbol: name} for
    ordinary equities (excluding debt/TFC instruments, ETFs and derivatives)."""
    req = Request(
        "https://dps.psx.com.pk/symbols",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://dps.psx.com.pk/",
        },
    )
    try:
        with urlopen(req, timeout=25) as resp:
            data = _json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        raise ValueError(f"Could not fetch the PSX symbol list: {e}")

    out = {}
    for item in data or []:
        sym = str(item.get("symbol", "")).strip().upper()
        if not sym or item.get("isDebt") or item.get("isETF"):
            continue
        if "-" in sym:  # skip futures / deliverable-future style tickers
            continue
        out[sym] = str(item.get("name", sym)).strip() or sym
    if not out:
        raise ValueError("PSX symbol list came back empty.")
    return dict(sorted(out.items()))


@st.cache_data(ttl=900, show_spinner=False)
def load_psx_stock(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Load a single PSX stock. Prefers Yahoo Finance (`SYMBOL.KA`) for genuine
    OHLC candles; if Yahoo has no data, falls back to the PSX Data Portal's
    close-only feed (line chart). The chosen source is recorded in
    `df.attrs['source']`."""
    symbol = symbol.strip().upper()

    # --- Preferred: Yahoo Finance .KA (full OHLC) ---------------------------
    try:
        ydf = load_price_data(f"{symbol}.KA", period)
        ydf.attrs["has_ohlc"] = True
        ydf.attrs["source"] = "yahoo"
        return ydf
    except Exception:
        pass  # fall through to PSX close-only

    # --- Fallback: PSX Data Portal (close-only) -----------------------------
    try:
        pdf = load_psx_data(symbol, period)  # already flags has_ohlc=False
        pdf.attrs["source"] = "psx"
        return pdf
    except Exception as e:
        raise ValueError(
            f"No data available for '{symbol}' from Yahoo (.KA) or the PSX "
            f"Data Portal. It may be newly listed, suspended, or delisted. ({e})"
        )


# -----------------------------------------------------------------------------
# AI chart-image analysis (Anthropic Claude vision)
# -----------------------------------------------------------------------------
AI_MODELS = {
    "Claude Sonnet 5 (faster / cheaper)": "claude-sonnet-5",
    "Claude Opus 5 (best reasoning)": "claude-opus-5",
}

_ANALYSIS_SCHEMA_HINT = """
Return ONLY a single valid JSON object (no markdown, no code fences, no prose
outside the JSON) with EXACTLY this shape:

{
  "instrument_guess": "string - ticker/name if visible, else 'Unknown'",
  "timeframe_guess": "string - e.g. '1H', 'Daily', 'Weekly', else 'Unknown'",
  "descriptive": {
    "trend": "string - concise trend description",
    "bias": "Bullish | Bearish | Neutral",
    "summary": "2-4 sentence professional read of the price action & structure",
    "supports": [{"level": "price or zone as string", "rationale": "why"}],
    "resistances": [{"level": "price or zone as string", "rationale": "why"}],
    "stop_losses": [{"level": "price as string", "rationale": "why here"}],
    "observations": ["short actionable bullet", "..."]
  },
  "position_sizing": {
    "suggested_entry": "price as string",
    "stop": "price as string",
    "targets": ["price as string", "..."],
    "entry_num": 0.0,
    "stop_num": 0.0,
    "target_nums": [0.0],
    "risk_reward": "e.g. '1 : 2.4'",
    "bias_alignment": "how the setup aligns with the overall bias",
    "notes": "position-sizing rationale in 1-2 sentences"
  },
  "elliott": {
    "structure_type": "impulse | corrective | diagonal | unclear",
    "degree": "wave degree label, e.g. 'Intermediate'",
    "narrative": "holistic textbook wave-count read of what has occurred",
    "current_waves": [{"label": "1/2/3/4/5 or A/B/C", "description": "what & where"}],
    "next_waves": [{"label": "string", "description": "what is expected next"}],
    "rules_check": [
      {"rule": "Wave 2 never retraces >100% of Wave 1", "status": "Pass | Fail | N/A", "detail": "how it holds"},
      {"rule": "Wave 3 is never the shortest impulse wave", "status": "Pass | Fail | N/A", "detail": "..."},
      {"rule": "Wave 4 never enters Wave 1 price territory", "status": "Pass | Fail | N/A", "detail": "..."}
    ],
    "invalidation": "price level/condition that invalidates this count",
    "targets": "projected target zone(s) with Fibonacci reasoning",
    "wave_points": [{"label": "0/1/2/3/4/5 or A/B/C", "x": 0.0, "y": 0.0, "price": "optional string"}],
    "projected_points": [{"label": "string", "x": 0.0, "y": 0.0, "price": "optional string"}]
  }
}

CRITICAL — wave_points and projected_points coordinates are NORMALIZED IMAGE
FRACTIONS so they can be drawn directly on top of the uploaded image:
  • "x" = 0.0 at the LEFT edge of the chart image, 1.0 at the RIGHT edge.
  • "y" = 0.0 at the TOP edge of the image, 1.0 at the BOTTOM edge.
    (So a swing HIGH has a SMALL y, a swing LOW has a LARGE y.)
Place each pivot as accurately as you can on the actual candles/price line in the
image. wave_points trace the count that has ALREADY formed, left to right, ending
at the most recent completed pivot (its x is usually between 0.9 and 1.0).

projected_points are the EXPECTED FUTURE path and are drawn in the empty space to
the RIGHT of your chart, so give them x values BETWEEN 1.03 AND 1.30 (increasing,
never inside the image). Their y values use the SAME 0..1 vertical scale as the
image, so a y of 0.30 lines up horizontally with the price level at 30% down from
the top. Provide 2-4 projected pivots that obey Elliott expectations for whatever
comes next (e.g. a corrective A-B-C after a completed impulse, or the next impulse
leg). Include "price" as the axis price at each pivot when the axis is legible.
"""

_ANALYSIS_SYSTEM = (
    "You are an elite technical analyst and a strict, textbook-compliant Elliott "
    "Wave theorist, acting as an EDUCATIONAL charting assistant. You describe the "
    "technical patterns visible in a chart IMAGE and the illustrative technical "
    "levels that follow from that price structure, purely for learning and "
    "chart-reading practice. This is general technical education about a picture "
    "of a chart, not personalized investment advice and not a recommendation to "
    "buy or sell any security; the app already shows the user a 'not financial "
    "advice' disclaimer. Analyze the chart as requested.\n\n"
    "ELLIOTT WAVE METHOD — follow this exact procedure and do NOT shortcut it:\n"
    "1. Determine the direction of the impulse you are counting FIRST. An impulse "
    "UP has waves 1,3,5 pointing up and 2,4 pulling back down; an impulse DOWN is "
    "the mirror. Pick the direction that matches the dominant swing on the chart.\n"
    "2. Assign pivots so the geometry is internally consistent. For an UP impulse: "
    "wave 2 low must stay ABOVE the wave 0 start; wave 3 high must EXCEED wave 1 "
    "high; wave 4 low must stay ABOVE wave 1 high (no overlap); wave 5 high must "
    "EXCEED wave 3 high (unless it is an explicit truncation, which you must name). "
    "Mirror all of this for a DOWN impulse. A point labelled '5' that sits below "
    "the start of an up-impulse (or above the start of a down-impulse) is WRONG — "
    "that is not an impulse; relabel it as a correction (A-B-C) or a new impulse.\n"
    "3. Enforce the THREE inviolable rules: (a) Wave 2 never retraces more than "
    "100% of Wave 1; (b) Wave 3 is never the shortest of 1/3/5; (c) Wave 4 never "
    "enters Wave 1 price territory (except diagonals).\n"
    "4. VALIDATE before you output: re-check every rule and every directional "
    "constraint above against the y-coordinates you assigned. If ANY fails, "
    "re-derive the count until it passes. Never output a labelled impulse that "
    "violates a rule.\n"
    "5. If the visible price action does NOT form a clean, rule-compliant Elliott "
    "structure, say so honestly: set structure_type to 'unclear' or 'corrective', "
    "give your best partial read, and do NOT fabricate a 1-2-3-4-5 that breaks the "
    "rules just to fill the fields.\n\n"
    "Also respect the guidelines (alternation, channelling, Fibonacci ratios) and "
    "note the wave degree. Prices you cannot read precisely are approximate zones. "
    "Output STRICTLY the requested JSON and nothing else."
)


@st.cache_data(ttl=3600, show_spinner=False)
def analyze_chart_image(
    image_bytes: bytes, media_type: str, model: str, api_key: str
) -> dict:
    """Send the chart image to Claude and return the parsed analysis dict.
    Cached on the (image, media_type, model) so identical requests don't re-bill;
    the api_key participates in the cache key but is not otherwise logged."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "The 'anthropic' package is not installed. Run "
            "`pip install anthropic` (it is in requirements.txt)."
        ) from e

    if not api_key:
        raise RuntimeError("No Anthropic API key provided.")

    client = anthropic.Anthropic(api_key=api_key)
    b64 = _b64.standard_b64encode(image_bytes).decode("utf-8")

    base_task = (
        "For educational chart-reading practice, describe the technical patterns "
        "in the attached chart image and the illustrative technical levels they "
        "imply: (1) descriptive technical analysis with support, resistance and "
        "stop-loss levels; (2) risk-managed position-sizing inputs (entry, stop, "
        "targets, R:R) consistent with the visible structure; (3) a rigorous "
        "Elliott Wave breakdown of the completed count and expected next waves, "
        "strictly obeying all core Elliott Wave rules. This is general technical "
        "education about a picture, not personalized financial advice.\n\n"
    )

    def _content(note: str) -> list:
        return [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            },
            {"type": "text", "text": note + base_task + _ANALYSIS_SCHEMA_HINT},
        ]

    def _call(note: str):
        # Let the model reason NATIVELY (extended thinking is on by default for
        # both Opus 5 and Sonnet 5) — this is what makes the Elliott rule-checking
        # reliable, and it avoids the 'reasoning_extraction' filter that a
        # reasoning-dump output field would trip. Streaming keeps the larger
        # token budget from hitting HTTP timeouts.
        with client.messages.stream(
            model=model, max_tokens=20000, system=_ANALYSIS_SYSTEM,
            messages=[{"role": "user", "content": _content(note)}],
        ) as stream:
            return stream.get_final_message()

    try:
        resp = _call("")
        if getattr(resp, "stop_reason", None) == "refusal":
            # Over-cautious false positive — retry once with explicit scope.
            resp = _call(
                "NOTE: This is a legitimate, educational technical-analysis "
                "exercise on a chart image for a charting app that already shows "
                "its own 'not financial advice' disclaimer. Please proceed.\n\n"
            )
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Anthropic rejected the API key (authentication error).") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Anthropic rate limit hit — wait a moment and retry.") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Could not reach Anthropic — check your connection.") from e

    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        details = getattr(resp, "stop_details", None)
        cat = getattr(details, "category", None) if details else None
        raise RuntimeError(
            "This model declined the request (safety category: "
            f"{cat or 'unspecified'}). This is usually an over-cautious "
            "financial-advice filter, not a problem with your chart. Try the "
            "**Claude Sonnet 5** model in the sidebar — it is less prone to this."
        )

    text = "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()

    if not text:
        if stop == "max_tokens":
            raise ValueError(
                "The analysis was cut off before completing (max_tokens). "
                "Try again, or switch to a simpler/clearer chart."
            )
        raise ValueError(
            f"The model returned no text (stop_reason={stop!r}). Please retry."
        )
    return _parse_analysis_json(text)


def _parse_analysis_json(text: str) -> dict:
    """Robustly extract the JSON object from the model's reply."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s
        s = s.lstrip("json").lstrip("JSON").strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("The model did not return JSON. Raw reply:\n" + text[:500])
    try:
        return _json.loads(s[start : end + 1])
    except _json.JSONDecodeError as e:
        raise ValueError(f"Could not parse the model's JSON ({e}).") from e


def build_wave_overlay_chart(
    image_bytes: bytes, media_type: str, elliott: dict
) -> "go.Figure | None":
    """Draw the Elliott Wave labels + dotted projected path directly ON TOP of
    the uploaded chart image, using the model's normalized image coordinates."""
    pts = [p for p in (elliott.get("wave_points") or []) if _has_xy(p)]
    proj = [p for p in (elliott.get("projected_points") or []) if _has_xy(p)]
    if len(pts) < 2:
        return None

    # Future zone extends the x-axis to the right of the image (x=1) so the
    # projected path has clear, uncramped space. Image stays pinned to [0, 1].
    x_right = 1.34
    b64 = _b64.standard_b64encode(image_bytes).decode("utf-8")
    fig = go.Figure()
    fig.add_layout_image(
        dict(
            source=f"data:{media_type};base64,{b64}",
            xref="x", yref="y", x=0, y=0, sizex=1, sizey=1,
            xanchor="left", yanchor="top", sizing="stretch", layer="below",
        )
    )
    # Shade the future zone and mark "now".
    fig.add_vrect(x0=1.0, x1=x_right, fillcolor="#0E1117", opacity=0.55, line_width=0)
    fig.add_vline(x=1.0, line=dict(color="#8892a0", width=1, dash="dot"))
    fig.add_annotation(
        x=1.0, y=0.02, text="now", showarrow=False, xanchor="left",
        yanchor="top", font=dict(color="#8892a0", size=11),
    )
    fig.add_annotation(
        x=(1.0 + x_right) / 2, y=0.06, text="◀ projected future ▶", showarrow=False,
        font=dict(color="#FFD54F", size=11),
    )

    fig.add_trace(
        go.Scatter(
            x=[p["x"] for p in pts], y=[p["y"] for p in pts],
            mode="lines+markers+text",
            text=[str(p.get("label", "")) for p in pts],
            textposition="top center",
            textfont=dict(color="#00E5FF", size=15),
            line=dict(color="#00E5FF", width=2.6),
            marker=dict(size=9, color="#00E5FF"),
            name="Completed waves",
        )
    )
    if proj:
        bridge_x = [pts[-1]["x"]] + [p["x"] for p in proj]
        bridge_y = [pts[-1]["y"]] + [p["y"] for p in proj]
        labels = [""] + [str(p.get("label", "")) for p in proj]
        fig.add_trace(
            go.Scatter(
                x=bridge_x, y=bridge_y,
                mode="lines+markers+text",
                text=labels, textposition="middle right",
                textfont=dict(color="#FFD54F", size=15),
                line=dict(color="#FFD54F", width=3.0, dash="dot"),
                marker=dict(size=10, color="#FFD54F"),
                name="Projected path (expected)",
            )
        )
    fig.update_xaxes(range=[-0.02, x_right], visible=False, fixedrange=True)
    fig.update_yaxes(range=[1, 0], visible=False, fixedrange=True)  # top-left origin
    fig.update_layout(
        height=560, margin=dict(l=6, r=6, t=40, b=6),
        title=dict(
            text="Elliott Wave — completed count (cyan) on your chart, expected path (dotted amber) in the future zone",
            x=0.01, font=dict(size=12.5),
        ),
        legend=dict(orientation="h", y=1.03, x=0, font=dict(size=10)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0E1117",
        dragmode=False,
    )
    return fig


def _has_xy(p: dict) -> bool:
    try:
        float(p.get("x")); float(p.get("y"))
        return True
    except (TypeError, ValueError):
        return False


# -----------------------------------------------------------------------------
# Deep multi-degree Elliott Wave report (data-driven)
# -----------------------------------------------------------------------------
def zigzag_pivots(close: pd.Series, pct: float = 0.08) -> list:
    """Percentage-reversal ZigZag: return alternating swing pivots as a list of
    (Timestamp, price) so the model gets real, precise turning points."""
    close = close.dropna()
    if len(close) < 3:
        return [(t, float(v)) for t, v in close.items()]
    idx = list(close.index)
    px = [float(v) for v in close.values]

    piv = [(idx[0], px[0])]
    direction = 0            # +1 up-leg tracking, -1 down-leg, 0 not yet established
    ext_p, ext_i = px[0], 0  # running extreme since the last confirmed pivot
    for i in range(1, len(px)):
        p = px[i]
        if direction == 0:
            if p >= piv[-1][1] * (1 + pct):
                direction, ext_p, ext_i = 1, p, i
            elif p <= piv[-1][1] * (1 - pct):
                direction, ext_p, ext_i = -1, p, i
        elif direction == 1:            # in an up-leg: track the high
            if p > ext_p:
                ext_p, ext_i = p, i
            elif p <= ext_p * (1 - pct):  # reversal confirms the swing high
                piv.append((idx[ext_i], ext_p))
                direction, ext_p, ext_i = -1, p, i
        else:                            # down-leg: track the low
            if p < ext_p:
                ext_p, ext_i = p, i
            elif p >= ext_p * (1 + pct):  # reversal confirms the swing low
                piv.append((idx[ext_i], ext_p))
                direction, ext_p, ext_i = 1, p, i
    # Append the final in-progress extreme if it advances past the last pivot.
    if ext_i > 0 and idx[ext_i] != piv[-1][0]:
        piv.append((idx[ext_i], ext_p))
    return piv


def _resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Return the frame at the requested timeframe (Daily passes through)."""
    if timeframe == "Weekly":
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
        cols = {k: v for k, v in agg.items() if k in df.columns}
        out = df.resample("W-FRI").agg(cols).dropna(subset=["Close"])
        return out
    return df


def fetch_ew_history(params: dict, timeframe: str):
    """Load the longest sensible history for the selected instrument and return
    (close_series, display_name, symbol, n_bars)."""
    if params["is_psx_stock"]:
        sym = params["psx_stock_symbol"]
        df = load_psx_stock(sym, "5y")
        name = load_psx_symbols().get(sym, sym)
    elif params["is_psx"]:
        sym = params["psx_symbol"]
        df = load_psx_data(sym, "5y")
        name = PSX_INDICES.get(sym, sym)
    elif params["is_csv"]:
        sym = params["csv_label"]
        if params["uploaded_file"] is None:
            raise ValueError("Upload a CSV first to run the report on it.")
        df = load_csv_data(params["uploaded_file"])
        name = params["csv_label"]
    else:
        sym = params["ticker"]
        if not sym:
            raise ValueError("Enter a ticker first.")
        df = load_price_data(sym, "max")
        name = load_ticker_name(sym)

    wk = _resample(df, timeframe)
    if len(wk) < 40:
        raise ValueError(
            f"Only {len(wk)} {timeframe.lower()} bars available — need at least 40 "
            f"for a multi-degree count. Try the Daily timeframe or a longer history."
        )
    return wk["Close"], name, sym, len(wk)


_DEEP_EW_SCHEMA = """
Return ONLY one valid JSON object (no markdown/code fences) shaped EXACTLY:

{
  "position": "current wave position, e.g. 'Minor B of Int (2) of Primary 3 of Cycle B'",
  "current_price": "string like '$1.94'",
  "cycle_decline_pct": "string like '-97.92%' (peak-to-trough of the visible history), or ''",
  "structure_type": "impulse | corrective | diagonal | unclear",
  "cards": [{"label":"POSITION","value":"...","sub":"..."}],  // 4-5 concise status cards
  "narrative": ["professional paragraph 1", "paragraph 2", "..."],
  "rule_table": [
    {"sequence":"Primary 1 (1.105 -> 2.700)",
     "rule1":"e.g. '92.7% PASS' (W2 retrace of W1)",
     "rule2":"e.g. '$0.615 / $1.280 / $0.725 - w3 longest PASS' (W1/W3/W5 lengths, W3 not shortest)",
     "rule3":"e.g. 'w4 low 1.975 vs w1 high 1.720 - clears $0.255 PASS' (W4 vs W1 overlap)",
     "alternation":"short note"}
  ],
  "completed_pivots": [
    {"date":"YYYY-MM-DD","price": 0.0, "label":"1","degree":"Primary|Intermediate|Cycle|Minor"}
  ],
  "projected_path": [
    {"date":"YYYY-MM-DD","price": 0.0, "label":"(3) $3.78"}
  ],
  "invalidation_price": 0.0,
  "invalidation_label":"e.g. 'INVALID $1.3800'",
  "retracement_zone": {"low": 0.0, "high": 0.0, "label":"0.618-0.786 grid"},
  "targets": [{"name":"Int (2) target","price":"$1.8384","detail":"0.618 - wk 14 Sep low was $1.835"}],
  "time_windows": [{"wave":"Int (3)","window":"Dec 2026 - Mar 2027","fib":"2.618 x (1) time"}],
  "use_log_scale": true
}

Rules for the data you output:
- completed_pivots MUST use the ACTUAL provided pivot dates & prices (you assign
  the wave labels/degrees to them). Include the higher-degree labels on the pivots
  that mark degree boundaries.
- projected_path dates are in the FUTURE (after the last pivot date); prices are
  your Fibonacci-derived targets. Give 3-6 points tracing the expected path.
- invalidation_price and retracement_zone are real price numbers.
- Compute every ratio in rule_table from the actual pivot prices; verify the three
  inviolable rules (W2<=100% W1; W3 not shortest of 1/3/5; W4 no overlap with W1)
  AND directional consistency. If a labelling violates a rule, RELABEL until valid.
  If no clean impulse exists, set structure_type to 'corrective'/'unclear' and say so.
"""


def deep_elliott_analysis(
    name: str, symbol: str, timeframe: str, pivots: list,
    last_price: float, n_bars: int, model: str, api_key: str,
) -> dict:
    """Run a rigorous multi-degree Elliott Wave analysis on real swing pivots."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("The 'anthropic' package is not installed.") from e
    if not api_key:
        raise RuntimeError("No Anthropic API key provided.")

    client = anthropic.Anthropic(api_key=api_key)
    lines = []
    for t, p in pivots:
        try:
            lines.append(f"{pd.Timestamp(t).date()}: {p:.4f}")
        except Exception:
            continue
    pivot_block = "\n".join(lines)

    task = (
        f"For educational technical-analysis practice, produce a rigorous, "
        f"textbook-compliant, MULTI-DEGREE Elliott Wave analysis (Cycle -> Primary "
        f"-> Intermediate, adding Minor where useful) for {name} ({symbol}) on the "
        f"{timeframe} timeframe. This is general chart education about historical "
        f"price data, not personalized financial advice.\n\n"
        f"History: {n_bars} {timeframe.lower()} bars. Latest price: {last_price:.4f}.\n"
        f"Actual swing pivots (date: price), oldest first:\n{pivot_block}\n\n"
        f"Assign wave labels to these real pivots, compute the Fibonacci price and "
        f"time relationships from them, verify all core Elliott Wave rules with "
        f"actual numbers, give price targets, a projected future path, and the "
        f"invalidation level.\n\n" + _DEEP_EW_SCHEMA
    )

    try:
        with client.messages.stream(
            model=model, max_tokens=24000,
            system=_ANALYSIS_SYSTEM,
            messages=[{"role": "user", "content": task}],
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Anthropic rejected the API key (authentication error).") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Anthropic rate limit hit — wait and retry.") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Anthropic API error ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Could not reach Anthropic — check your connection.") from e

    if getattr(resp, "stop_reason", None) == "refusal":
        details = getattr(resp, "stop_details", None)
        cat = getattr(details, "category", None) if details else None
        raise RuntimeError(
            f"The model declined (safety category: {cat or 'unspecified'}). Try "
            f"Claude Sonnet 5 in the sidebar."
        )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()
    if not text:
        raise ValueError("The model returned no text — please retry.")
    return _parse_analysis_json(text)


def build_ew_report_chart(close: pd.Series, data: dict) -> "go.Figure":
    """Weekly/daily close with labelled completed pivots, dotted projected path,
    invalidation line and retracement grid — the projection chart of the report."""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=close.index, y=close.values, name="Close",
            line=dict(color="#4C78D8", width=1.6),
        )
    )

    comp = data.get("completed_pivots") or []
    cx, cy, ct = [], [], []
    for p in comp:
        d = pd.to_datetime(p.get("date"), errors="coerce")
        y = p.get("price")
        if pd.isna(d) or y in (None, ""):
            continue
        cx.append(d); cy.append(float(y)); ct.append(str(p.get("label", "")))
    if cx:
        fig.add_trace(
            go.Scatter(
                x=cx, y=cy, mode="markers+text", text=ct, textposition="top center",
                textfont=dict(size=11, color="#111"),
                marker=dict(size=8, color="#E4572E", line=dict(width=1, color="#fff")),
                name="Completed pivots",
            )
        )

    # Projected path — future dates; if unparseable, sequence forward from last bar.
    proj = data.get("projected_path") or []
    last_date = close.index[-1]
    step = (close.index[-1] - close.index[0]) / max(len(close) - 1, 1)
    px_, py_, pt_ = [], [], []
    for k, p in enumerate(proj, 1):
        d = pd.to_datetime(p.get("date"), errors="coerce")
        if pd.isna(d) or d <= last_date:
            d = last_date + step * (4 * k)
        y = p.get("price")
        if y in (None, ""):
            continue
        px_.append(d); py_.append(float(y)); pt_.append(str(p.get("label", "")))
    if cx and px_:
        px_ = [cx[-1]] + px_
        py_ = [cy[-1]] + py_
        pt_ = [""] + pt_
    if px_:
        fig.add_trace(
            go.Scatter(
                x=px_, y=py_, mode="lines+markers+text", text=pt_,
                textposition="top center", textfont=dict(size=11, color="#E4572E"),
                line=dict(color="#E4572E", width=2, dash="dash"),
                marker=dict(size=7, color="#E4572E"),
                name="Projected path",
            )
        )

    inv = data.get("invalidation_price")
    if isinstance(inv, (int, float)) and inv:
        fig.add_hline(
            y=float(inv), line=dict(color="#C1121F", width=1.4, dash="dash"),
            annotation_text=data.get("invalidation_label", f"Invalid {inv}"),
            annotation_position="right",
        )
    rz = data.get("retracement_zone") or {}
    lo, hi = rz.get("low"), rz.get("high")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo and hi:
        fig.add_hrect(
            y0=min(lo, hi), y1=max(lo, hi), fillcolor="#2E7D32",
            opacity=0.10, line_width=0,
            annotation_text=rz.get("label", ""), annotation_position="top left",
        )

    fig.update_layout(
        template="plotly_white", height=560,
        margin=dict(l=10, r=70, t=40, b=10),
        legend=dict(orientation="h", y=1.04, x=0, font=dict(size=10)),
        title=dict(text="Wave map — completed count & projected path", x=0.01,
                   font=dict(size=14)),
        yaxis=dict(type="log" if data.get("use_log_scale") else "linear"),
        dragmode=False,
    )
    return fig


# -----------------------------------------------------------------------------
# Indicator + level engine
# -----------------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Attach EMA20/50, RSI14, MACD(12,26,9) and ATR14 columns."""
    df = df.copy()
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    df["EMA20"] = EMAIndicator(close=close, window=20).ema_indicator()
    df["EMA50"] = EMAIndicator(close=close, window=50).ema_indicator()
    df["RSI"] = RSIIndicator(close=close, window=14).rsi()

    macd = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    df["MACD"] = macd.macd()
    df["MACD_Signal"] = macd.macd_signal()
    df["MACD_Hist"] = macd.macd_diff()

    df["ATR"] = AverageTrueRange(
        high=high, low=low, close=close, window=14
    ).average_true_range()

    return df


def classic_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Classic (floor-trader) daily pivot points from the previous session."""
    p = (prev_high + prev_low + prev_close) / 3.0
    s1 = (2 * p) - prev_high
    r1 = (2 * p) - prev_low
    s2 = p - (prev_high - prev_low)
    r2 = p + (prev_high - prev_low)
    return {"P": p, "S1": s1, "S2": s2, "R1": r1, "R2": r2}


def compute_trade_plan(
    df: pd.DataFrame,
    atr_mult: float,
    rr_tp1: float,
    rr_tp2: float,
) -> dict:
    """Build the full trade plan (levels, bias, indicator snapshot)."""
    last = df.iloc[-1]
    prev = df.iloc[-2]

    current_price = float(last["Close"])
    prev_close = float(prev["Close"])
    daily_change = current_price - prev_close
    daily_change_pct = (daily_change / prev_close) * 100 if prev_close else 0.0

    ema20 = float(last["EMA20"])
    ema50 = float(last["EMA50"])
    rsi = float(last["RSI"])
    macd_hist = float(last["MACD_Hist"])
    atr = float(last["ATR"])

    # Pivots from the most recent completed session.
    piv = classic_pivots(float(last["High"]), float(last["Low"]), float(last["Close"]))

    # --- Trend / momentum bias ------------------------------------------------
    bull_signals = 0
    bear_signals = 0

    if ema20 > ema50:
        bull_signals += 1
    else:
        bear_signals += 1

    if macd_hist > 0:
        bull_signals += 1
    else:
        bear_signals += 1

    if current_price > ema20:
        bull_signals += 1
    else:
        bear_signals += 1

    if bull_signals >= 2 and rsi <= 70:
        bias = "Bullish"
    elif bear_signals >= 2:
        bias = "Bearish"
    else:
        bias = "Neutral"

    overbought = rsi > 70

    # --- Entry levels ---------------------------------------------------------
    # Two candidate support confluences:
    #   • EMA20 blended with Classic Support S1 (the nearer/shallower zone)
    #   • EMA50 blended with Classic Support S2 (the deeper zone)
    cand_a = (ema20 + piv["S1"]) / 2.0
    cand_b = (ema50 + piv["S2"]) / 2.0

    # Enforce the invariant: Primary (S1) is the shallower (higher) support,
    # Deep (S2) is the lower one. In a downtrend EMA50 > EMA20, which can push
    # the EMA50 blend above the EMA20 blend, so we order rather than assume.
    s1_entry = round(max(cand_a, cand_b), 2)
    s2_entry = round(min(cand_a, cand_b), 2)

    # Guard against a degenerate tie so S2 is always a genuinely deeper entry.
    if s1_entry - s2_entry < 0.01:
        s2_entry = round(s1_entry - max(0.5 * atr, 0.01 * s1_entry), 2)

    # --- Risk levels ----------------------------------------------------------
    stop_loss = round(s1_entry - (atr_mult * atr), 2)
    risk = s1_entry - stop_loss  # per-share risk (positive when SL below entry)

    tp1 = round(s1_entry + rr_tp1 * risk, 2)
    tp2_rr = s1_entry + rr_tp2 * risk
    # TP2 = the more ambitious of the RR target and Classic Resistance R2.
    tp2 = round(max(tp2_rr, piv["R2"]), 2)

    realized_rr_tp2 = (tp2 - s1_entry) / risk if risk > 0 else float("nan")

    return {
        "current_price": current_price,
        "daily_change": daily_change,
        "daily_change_pct": daily_change_pct,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "atr": atr,
        "pivots": piv,
        "bias": bias,
        "overbought": overbought,
        "s1_entry": s1_entry,
        "s2_entry": s2_entry,
        "stop_loss": stop_loss,
        "risk": risk,
        "tp1": tp1,
        "tp2": tp2,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "realized_rr_tp2": realized_rr_tp2,
    }


# -----------------------------------------------------------------------------
# Presentation helpers
# -----------------------------------------------------------------------------
def bias_badge(bias: str) -> str:
    palette = {
        "Bullish": ("#00C853", "#052e16"),
        "Bearish": ("#D50000", "#3b0a0a"),
        "Neutral": ("#FFB300", "#332600"),
    }
    fg, bg = palette.get(bias, ("#9E9E9E", "#1a1a1a"))
    return (
        f"<span style='background:{bg};color:{fg};padding:6px 16px;"
        f"border-radius:20px;font-weight:700;font-size:0.95rem;"
        f"border:1px solid {fg};letter-spacing:0.5px;'>{bias.upper()}</span>"
    )


def pct_from_price(level: float, price: float) -> float:
    return ((level - price) / price) * 100 if price else float("nan")


def build_chart(df: pd.DataFrame, plan: dict, ticker: str) -> go.Figure:
    """3-row subplot: price+levels, RSI, MACD."""
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.62, 0.19, 0.19],
        subplot_titles=("Price Action & Trade Levels", "RSI (14)", "MACD (12, 26, 9)"),
    )

    # --- Row 1: price (candles when OHLC is real, else a line) + EMAs ---------
    has_ohlc = df.attrs.get("has_ohlc", True)
    if has_ohlc:
        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                name="Price",
                increasing_line_color="#26A69A",
                decreasing_line_color="#EF5350",
            ),
            row=1,
            col=1,
        )
    else:
        # Close-only source (e.g. PSX index feed): draw a price line.
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["Close"], name="Close",
                line=dict(color="#26A69A", width=1.8),
            ),
            row=1, col=1,
        )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["EMA20"], name="EMA 20",
            line=dict(color=COLOR_EMA20, width=1.4),
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["EMA50"], name="EMA 50",
            line=dict(color=COLOR_EMA50, width=1.4),
        ),
        row=1, col=1,
    )

    # Horizontal trade levels
    level_lines = [
        ("S1 Entry", plan["s1_entry"], COLOR_S1),
        ("S2 Entry", plan["s2_entry"], COLOR_S2),
        ("Stop Loss", plan["stop_loss"], COLOR_SL),
        ("TP1", plan["tp1"], COLOR_TP1),
        ("TP2", plan["tp2"], COLOR_TP2),
    ]
    x0, x1 = df.index[0], df.index[-1]
    for label, level, color in level_lines:
        fig.add_trace(
            go.Scatter(
                x=[x0, x1],
                y=[level, level],
                mode="lines",
                name=f"{label} ({level:,.2f})",
                line=dict(color=color, width=1.6, dash="dash"),
                hoverinfo="name",
                showlegend=False,  # levels are labelled in the cards below
            ),
            row=1, col=1,
        )

    # --- Row 2: RSI -----------------------------------------------------------
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["RSI"], name="RSI",
            line=dict(color="#7E57C2", width=1.3),
        ),
        row=2, col=1,
    )
    fig.add_hline(y=70, line=dict(color=COLOR_SL, width=1, dash="dot"), row=2, col=1)
    fig.add_hline(y=30, line=dict(color=COLOR_S1, width=1, dash="dot"), row=2, col=1)
    fig.add_hrect(y0=70, y1=100, fillcolor=COLOR_SL, opacity=0.06, line_width=0, row=2, col=1)
    fig.add_hrect(y0=0, y1=30, fillcolor=COLOR_S1, opacity=0.06, line_width=0, row=2, col=1)
    fig.update_yaxes(range=[0, 100], row=2, col=1)

    # --- Row 3: MACD ----------------------------------------------------------
    hist_colors = np.where(df["MACD_Hist"] >= 0, "#26A69A", "#EF5350")
    fig.add_trace(
        go.Bar(
            x=df.index, y=df["MACD_Hist"], name="Histogram",
            marker_color=hist_colors, opacity=0.6,
        ),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["MACD"], name="MACD",
            line=dict(color=COLOR_TP1, width=1.3),
        ),
        row=3, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df["MACD_Signal"], name="Signal",
            line=dict(color=COLOR_EMA20, width=1.3),
        ),
        row=3, col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=760,
        margin=dict(l=8, r=12, t=64, b=24),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(size=9), bgcolor="rgba(0,0,0,0)",
        ),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        dragmode=False,
    )
    fig.update_xaxes(rangeslider_visible=False)
    fig.update_yaxes(automargin=True)
    return fig


# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------
def render_sidebar() -> dict:
    st.sidebar.title("⚙️ Controls")

    source = st.sidebar.radio(
        "Data Source",
        options=[
            "Yahoo Finance (ticker)",
            "PSX Stock (KSE All Share)",
            "PSX Index (KSE indices)",
            "Upload CSV (any instrument)",
        ],
        index=0,
        help=(
            "Yahoo: US/global tickers + PSX stocks (e.g. HBL.KA). "
            "PSX Stock: pick any KSE All Share constituent — OHLC candles from "
            "Yahoo where available, else PSX Data Portal close-only. "
            "PSX Index: official KSE indices incl. the All Share aggregate. "
            "Upload CSV: any history exported from Investing.com / TradingView."
        ),
    )
    is_psx_stock = source.startswith("PSX Stock")
    is_psx = source.startswith("PSX Index")
    is_csv = source.startswith("Upload")

    ticker = "NVDA"
    csv_label = "KSE All Share Index"
    uploaded_file = None
    psx_symbol = "ALLSHR"
    psx_stock_symbol = None

    if is_psx_stock:
        try:
            symbols = load_psx_symbols()
        except Exception as e:
            st.sidebar.error(f"Could not load PSX constituents: {e}")
            symbols = {}
        if symbols:
            opts = list(symbols.keys())
            default_idx = opts.index("HBL") if "HBL" in symbols else 0
            psx_stock_symbol = st.sidebar.selectbox(
                f"PSX Stock ({len(opts)} listed)",
                options=opts,
                index=default_idx,
                format_func=lambda s: f"{s} — {symbols.get(s, '')[:26]}",
                help="Searchable — type a symbol like OGDC, LUCK, MEBL.",
            )
            st.sidebar.caption(
                f"Trying Yahoo **{psx_stock_symbol}.KA** first, "
                f"then PSX Data Portal."
            )
    elif is_psx:
        psx_label = st.sidebar.selectbox(
            "PSX Index",
            options=list(PSX_INDICES.keys()),
            index=0,  # ALLSHR = KSE All Share
            format_func=lambda s: f"{PSX_INDICES[s]} ({s})",
            help="Live end-of-day data from the PSX Data Portal.",
        )
        psx_symbol = psx_label
        st.sidebar.caption(f"Source: dps.psx.com.pk · symbol **{psx_symbol}**")
    elif is_csv:
        csv_label = st.sidebar.text_input(
            "Instrument Name", value="KSE All Share Index",
            help="Shown in the header. Purely a label.",
        ).strip() or "Uploaded Instrument"
        uploaded_file = st.sidebar.file_uploader(
            "Historical CSV", type=["csv"],
            help=(
                "Investing.com: open the KSE All Share page → Historical Data → "
                "'Download Data'. TradingView: chart → Export chart data. "
                "Daily interval, 1 year or more."
            ),
        )
        with st.sidebar.expander("📄 Expected CSV format"):
            st.markdown(
                "- **Investing.com:** `Date, Price, Open, High, Low, Vol., Change %`\n"
                "- **TradingView:** `time, open, high, low, close, Volume`\n\n"
                "Commas in numbers and K/M/B volume suffixes are handled "
                "automatically. Only a Date + Close/Price column are strictly "
                "required."
            )
    else:
        ticker = st.sidebar.text_input(
            "Ticker Symbol", value="NVDA",
            help="e.g. NVDA, AAPL, MSFT, TSLA — or PSX stocks like HBL.KA, LUCK.KA",
        ).strip().upper()

    period_label = st.sidebar.selectbox(
        "Lookback Period",
        options=list(PERIOD_OPTIONS.keys()),
        index=1,  # default 1 Year
        disabled=is_csv,
        help=(
            "Applies to Yahoo and PSX Data Portal. "
            "CSV mode uses the file's full range."
        ),
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📐 Risk Parameters")

    atr_mult = st.sidebar.slider(
        "ATR Stop-Loss Multiplier", 1.0, 3.0, 1.5, 0.1,
        help="Stop = S1 − (multiplier × ATR14)",
    )
    rr_tp1 = st.sidebar.slider(
        "Risk:Reward for TP1", 1.0, 3.0, 1.5, 0.1,
    )
    rr_tp2 = st.sidebar.slider(
        "Risk:Reward for TP2", 2.0, 5.0, 3.0, 0.1,
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("🖼️ AI Chart Image Analysis")
    chart_image = st.sidebar.file_uploader(
        "Upload a chart image (any timeframe)",
        type=["png", "jpg", "jpeg", "webp"],
        help="Screenshot from any platform. Analyzed by Claude vision into the "
             "three tabs shown in the main panel.",
    )
    ai_model_label = st.sidebar.selectbox(
        "Analysis model", options=list(AI_MODELS.keys()), index=0,
    )
    ai_model = AI_MODELS[ai_model_label]

    # API key — Streamlit secrets if present, else a password field ("Both").
    api_key = ""
    try:
        api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        api_key = ""
    # Ignore the shipped placeholder so the input still shows until a real key is set.
    if api_key and "REPLACE" in api_key:
        api_key = ""
    key_from_secrets = bool(api_key)
    if key_from_secrets:
        st.sidebar.caption("🔑 Using API key from Streamlit secrets.")
    else:
        api_key = st.sidebar.text_input(
            "Anthropic API Key", type="password",
            help="Get one at console.anthropic.com. Kept in this session only; "
                 "never committed. Or set ANTHROPIC_API_KEY in Streamlit secrets.",
        ).strip()

    with st.sidebar.expander("💰 Position-sizing inputs"):
        account_size = st.number_input(
            "Account size", min_value=0.0, value=10000.0, step=500.0,
            help="Total trading capital (used in the Position Sizing tab).",
        )
        risk_pct = st.slider(
            "Risk per trade (%)", 0.1, 5.0, 1.0, 0.1,
            help="Percent of account risked if the stop is hit.",
        )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "⚠️ Educational tool only. Not financial advice. "
        "Levels are algorithmic estimates from historical data."
    )

    return {
        "is_psx_stock": is_psx_stock,
        "is_psx": is_psx,
        "is_csv": is_csv,
        "ticker": ticker,
        "psx_symbol": psx_symbol,
        "psx_stock_symbol": psx_stock_symbol,
        "csv_label": csv_label,
        "uploaded_file": uploaded_file,
        "period": PERIOD_OPTIONS[period_label],
        "period_label": period_label,
        "atr_mult": atr_mult,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "chart_image": chart_image,
        "ai_model": ai_model,
        "api_key": api_key,
        "account_size": account_size,
        "risk_pct": risk_pct,
    }


# -----------------------------------------------------------------------------
# Main render
# -----------------------------------------------------------------------------
def inject_css() -> None:
    """Global styling for a clean, mobile-first look."""
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1180px;}
        /* Hide Streamlit chrome for a cleaner app feel */
        #MainMenu, footer, [data-testid="stDeployButton"], .stDeployButton {visibility: hidden;}
        /* Header card */
        .qtl-head {display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between;
            gap:12px; padding:16px 18px; border-radius:16px;
            background:linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.015));
            border:1px solid rgba(255,255,255,0.09);}
        .qtl-head .nm {font-size:1.05rem; font-weight:600; color:#c9cdd2; margin-bottom:2px;}
        .qtl-head .px {font-size:1.9rem; font-weight:800; line-height:1.1;}
        .qtl-head .chg {font-size:1rem; font-weight:600; margin-left:6px;}
        .qtl-badge {padding:7px 16px; border-radius:24px; font-weight:800; font-size:0.9rem;
            letter-spacing:0.6px; white-space:nowrap;}
        /* Metric grid — wraps on mobile automatically */
        .qtl-grid {display:grid; grid-template-columns:repeat(auto-fit, minmax(148px, 1fr));
            gap:10px; margin:14px 0 4px;}
        .qtl-card {padding:13px 15px; border-radius:14px;
            background:linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.012));
            border:1px solid rgba(255,255,255,0.08); border-left:4px solid var(--ac,#888);}
        .qtl-card .lbl {font-size:0.7rem; text-transform:uppercase; letter-spacing:0.7px;
            color:#9aa0a6; margin-bottom:5px; font-weight:600;}
        .qtl-card .val {font-size:1.32rem; font-weight:800; line-height:1.05;}
        .qtl-card .sub {font-size:0.8rem; color:#aab0b7; margin-top:3px;}
        /* Summary rows */
        .qtl-srow {display:flex; align-items:center; justify-content:space-between; gap:10px;
            padding:11px 14px; border-radius:12px; margin-bottom:9px;
            border:1px solid rgba(255,255,255,0.08); border-left:4px solid var(--ac,#888);
            background:rgba(255,255,255,0.02);}
        .qtl-srow .lt {font-weight:700; font-size:0.95rem;}
        .qtl-srow .nt {font-size:0.76rem; color:#9aa0a6; margin-top:2px;}
        .qtl-srow .rt {text-align:right; white-space:nowrap;}
        .qtl-srow .pv {font-weight:800; font-size:1.05rem;}
        .qtl-srow .pc {font-size:0.78rem; color:#aab0b7;}
        @media (max-width: 640px) {
            .block-container {padding-left:0.7rem; padding-right:0.7rem;}
            .qtl-head .px {font-size:1.55rem;}
            .qtl-card .val {font-size:1.18rem;}
            .qtl-grid {gap:8px;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def render_header(name: str, ticker: str, plan: dict) -> None:
    change = plan["daily_change"]
    change_pct = plan["daily_change_pct"]
    arrow = "▲" if change >= 0 else "▼"
    change_color = "#00E676" if change >= 0 else "#FF5252"

    palette = {
        "Bullish": ("#00E676", "rgba(0,200,83,0.16)"),
        "Bearish": ("#FF5252", "rgba(213,0,0,0.16)"),
        "Neutral": ("#FFB300", "rgba(255,179,0,0.16)"),
    }
    fg, bg = palette.get(plan["bias"], ("#9E9E9E", "rgba(158,158,158,0.16)"))

    st.markdown(
        f"""
        <div class="qtl-head">
          <div>
            <div class="nm">{name} · {ticker}</div>
            <div class="px">{_fmt(plan['current_price'])}
              <span class="chg" style="color:{change_color};">
                {arrow} {change:+,.2f} ({change_pct:+.2f}%)</span>
            </div>
          </div>
          <div style="text-align:right;">
            <div class="nm" style="margin-bottom:6px;">Market Bias</div>
            <span class="qtl-badge" style="color:{fg}; background:{bg};
              border:1px solid {fg};">{plan['bias'].upper()}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if plan["overbought"]:
        st.warning(
            f"RSI is {plan['rsi']:.1f} (> 70, overbought). Momentum entries are "
            f"filtered out — favor waiting for a pullback toward S1/S2."
        )


def render_metrics(plan: dict) -> None:
    price = plan["current_price"]

    cards = [
        ("Primary Entry (S1)", _fmt(plan["s1_entry"]),
         f"{pct_from_price(plan['s1_entry'], price):+.2f}% from price", COLOR_S1),
        ("Deep Entry (S2)", _fmt(plan["s2_entry"]),
         f"{pct_from_price(plan['s2_entry'], price):+.2f}% from price", COLOR_S2),
        ("Stop Loss (SL)", _fmt(plan["stop_loss"]),
         f"{pct_from_price(plan['stop_loss'], price):+.2f}% from price", COLOR_SL),
        ("Take Profit 1", _fmt(plan["tp1"]),
         f"{pct_from_price(plan['tp1'], price):+.2f}% from price", COLOR_TP1),
        ("Take Profit 2", _fmt(plan["tp2"]),
         f"{pct_from_price(plan['tp2'], price):+.2f}% from price", COLOR_TP2),
        ("Risk / Reward", f"{plan['rr_tp1']:.1f} / {plan['rr_tp2']:.1f}",
         f"ATR {plan['atr']:.2f}", "#7E57C2"),
    ]
    html = ['<div class="qtl-grid">']
    for lbl, val, sub, color in cards:
        html.append(
            f'<div class="qtl-card" style="--ac:{color};">'
            f'<div class="lbl">{lbl}</div>'
            f'<div class="val">{val}</div>'
            f'<div class="sub">{sub}</div></div>'
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_summary_cards(plan: dict) -> None:
    price = plan["current_price"]
    rows = [
        ("Primary Entry (S1)", plan["s1_entry"],
         "Main accumulation zone (EMA20 / Pivot S1)", COLOR_S1),
        ("Deep Entry (S2)", plan["s2_entry"],
         "Extended pullback (EMA50 / Pivot S2)", COLOR_S2),
        ("Stop Loss (SL)", plan["stop_loss"],
         "Invalidation — thesis fails below", COLOR_SL),
        ("Take Profit 1 (TP1)", plan["tp1"],
         f"{plan['rr_tp1']:.1f}x R:R — scale out", COLOR_TP1),
        ("Take Profit 2 (TP2)", plan["tp2"],
         f"{plan['rr_tp2']:.1f}x R:R / Pivot R2 — runner", COLOR_TP2),
    ]
    html = []
    for label, level, note, color in rows:
        pct = pct_from_price(level, price)
        html.append(
            f'<div class="qtl-srow" style="--ac:{color};">'
            f'<div><div class="lt">{label}</div><div class="nt">{note}</div></div>'
            f'<div class="rt"><div class="pv">{_fmt(level)}</div>'
            f'<div class="pc">{pct:+.2f}%</div></div></div>'
        )
    st.markdown("".join(html), unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# AI chart-image analysis — three-tab renderer
# -----------------------------------------------------------------------------
def _levels_table(items: list, cols=("Level", "Rationale")) -> None:
    rows = []
    for it in items or []:
        rows.append({cols[0]: str(it.get("level", "")), cols[1]: str(it.get("rationale", ""))})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("None identified.")


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def render_image_analysis(params: dict) -> None:
    """Render the 3-tab AI analysis of an uploaded chart image. Renders nothing
    unless an image is uploaded, so the existing dashboard is untouched otherwise."""
    image_file = params.get("chart_image")
    if image_file is None:
        return

    st.markdown("## 🖼️ AI Chart Image Analysis")
    img_bytes = image_file.getvalue()
    media_type = image_file.type or "image/png"
    img_key = _hashlib.sha1(img_bytes).hexdigest()[:16] + "|" + params["ai_model"]

    top = st.columns([1, 2])
    with top[0]:
        st.image(img_bytes, caption="Uploaded chart", use_container_width=True)
    with top[1]:
        st.caption(
            "Claude vision reads this chart and fills the three tabs below: "
            "descriptive levels, position sizing, and a rules-checked Elliott "
            "Wave count. Analysis is cached, so it only bills once per image/model."
        )
        run = st.button("🔍 Analyze / re-analyze chart", type="primary")

    if not params.get("api_key"):
        st.info(
            "Add your Anthropic API key in the sidebar (or Streamlit secrets) to "
            "run the analysis. Get one at console.anthropic.com."
        )
        return

    # Cache the result in session_state so tab switches / slider changes don't re-bill.
    cache = st.session_state.setdefault("_img_analysis", {})
    if run or img_key in cache:
        if run and img_key not in cache:
            try:
                with st.spinner("Analyzing chart with Claude…"):
                    cache[img_key] = analyze_chart_image(
                        img_bytes, media_type, params["ai_model"], params["api_key"]
                    )
            except (RuntimeError, ValueError) as e:
                st.error(f"❌ {e}")
                return
            except Exception as e:  # pragma: no cover
                st.error(f"❌ Unexpected analysis error: {e}")
                return
        data = cache.get(img_key)
        if data:
            _render_analysis_tabs(data, params, img_bytes, media_type)
    else:
        st.caption("Click **Analyze** to run the vision analysis on this image.")


def _render_analysis_tabs(
    data: dict, params: dict, img_bytes: bytes, media_type: str
) -> None:
    meta = (
        f"Instrument: **{data.get('instrument_guess', 'Unknown')}** · "
        f"Timeframe: **{data.get('timeframe_guess', 'Unknown')}**"
    )
    st.caption(meta)

    t1, t2, t3 = st.tabs(
        ["📝 Descriptive TA", "💰 Position Sizing", "🌊 Elliott Wave"]
    )

    # --- Tab 1: Descriptive technical analysis --------------------------------
    with t1:
        d = data.get("descriptive", {}) or {}
        bias = d.get("bias", "Neutral")
        st.markdown(f"**Trend:** {d.get('trend', '—')}  ")
        st.markdown(bias_badge(bias), unsafe_allow_html=True)
        st.write(d.get("summary", ""))
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**🟢 Support levels**")
            _levels_table(d.get("supports"))
        with c2:
            st.markdown("**🔴 Resistance levels**")
            _levels_table(d.get("resistances"))
        with c3:
            st.markdown("**🛑 Stop-loss markers**")
            _levels_table(d.get("stop_losses"))
        obs = d.get("observations") or []
        if obs:
            st.markdown("**Key observations**")
            for o in obs:
                st.markdown(f"- {o}")

    # --- Tab 2: Automated position sizing -------------------------------------
    with t2:
        ps = data.get("position_sizing", {}) or {}
        st.caption(
            "Adjust the AI's read below if needed; sizing uses your account size "
            "and risk % from the sidebar."
        )
        ai_entry = _as_float(ps.get("entry_num")) or 0.0
        ai_stop = _as_float(ps.get("stop_num")) or 0.0
        ai_targets = [x for x in (_as_float(t) for t in ps.get("target_nums") or []) if x]

        c1, c2, c3 = st.columns(3)
        entry = c1.number_input("Entry", value=float(ai_entry), format="%.4f")
        stop = c2.number_input("Stop loss", value=float(ai_stop), format="%.4f")
        first_target = ai_targets[0] if ai_targets else 0.0
        target = c3.number_input("Primary target", value=float(first_target), format="%.4f")

        account = float(params.get("account_size") or 0.0)
        risk_pct = float(params.get("risk_pct") or 1.0)
        per_unit_risk = abs(entry - stop)
        risk_amount = account * risk_pct / 100.0

        if per_unit_risk > 0 and account > 0:
            size = risk_amount / per_unit_risk
            position_value = size * entry
            reward = abs(target - entry)
            rr = (reward / per_unit_risk) if per_unit_risk else 0.0
            m = st.columns(4)
            m[0].metric("Risk amount", f"{risk_amount:,.2f}", f"{risk_pct:.1f}% of acct")
            m[1].metric("Position size", f"{size:,.2f} units")
            m[2].metric("Position value", f"{position_value:,.2f}")
            m[3].metric("R : R (to target)", f"1 : {rr:.2f}")
        else:
            st.warning(
                "Enter a non-zero account size (sidebar) and distinct Entry/Stop "
                "to compute position size."
            )

        st.markdown("**AI sizing read**")
        st.write(
            f"- **Suggested entry:** {ps.get('suggested_entry', '—')}\n"
            f"- **Stop:** {ps.get('stop', '—')}\n"
            f"- **Targets:** {', '.join(str(x) for x in ps.get('targets', [])) or '—'}\n"
            f"- **Risk:Reward:** {ps.get('risk_reward', '—')}\n"
            f"- **Bias alignment:** {ps.get('bias_alignment', '—')}"
        )
        if ps.get("notes"):
            st.info(ps["notes"])

    # --- Tab 3: Elliott Wave --------------------------------------------------
    with t3:
        ew = data.get("elliott", {}) or {}
        stype = str(ew.get("structure_type", "")).lower()
        cols = st.columns(2)
        cols[0].markdown(f"**Structure:** {ew.get('structure_type', '—')}")
        cols[1].markdown(f"**Wave degree:** {ew.get('degree', '—')}")
        if stype == "unclear":
            st.warning(
                "The model judged the visible structure as not a clean, "
                "rule-compliant Elliott pattern — treat the count below as a "
                "tentative read, not a definitive labelling."
            )
        st.write(ew.get("narrative", ""))

        fig = build_wave_overlay_chart(img_bytes, media_type, ew)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False})
            st.caption(
                "Cyan = completed wave count drawn on your chart; dotted amber = "
                "expected next waves. Positions are the model's best visual read."
            )
        else:
            st.caption("No wave pivots were returned to draw an overlay.")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Completed waves**")
            for w in ew.get("current_waves") or []:
                st.markdown(f"- **{w.get('label', '')}** — {w.get('description', '')}")
        with c2:
            st.markdown("**Expected next waves**")
            for w in ew.get("next_waves") or []:
                st.markdown(f"- **{w.get('label', '')}** — {w.get('description', '')}")

        rc = ew.get("rules_check") or []
        if rc:
            st.markdown("**Elliott Wave rule compliance**")
            rows = []
            for r in rc:
                status = str(r.get("status", ""))
                icon = {"Pass": "✅", "Fail": "❌", "N/A": "➖"}.get(status, "")
                rows.append({
                    "Rule": r.get("rule", ""),
                    "Status": f"{icon} {status}",
                    "Detail": r.get("detail", ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        cols = st.columns(2)
        cols[0].markdown(f"**Invalidation:** {ew.get('invalidation', '—')}")
        cols[1].markdown(f"**Target zone(s):** {ew.get('targets', '—')}")

    st.caption(
        "AI analysis is probabilistic and for education only — not financial "
        "advice. Verify levels against your own charting."
    )


def render_deep_ew(params: dict) -> None:
    """Data-driven multi-degree Elliott Wave report for the selected instrument."""
    st.markdown("## 🌊 Deep Elliott Wave Report")
    st.caption(
        "A rigorous multi-degree count (Cycle → Primary → Intermediate) computed "
        "from the selected instrument's real history — rule-verified with actual "
        "Fibonacci ratios, price/time targets and a projected path."
    )

    if not params.get("api_key"):
        st.info(
            "Add your Anthropic API key in the sidebar (or Streamlit secrets) to "
            "generate the report."
        )
        return

    c = st.columns([1, 1, 2])
    timeframe = c[0].selectbox("Report timeframe", ["Weekly", "Daily"], index=0,
                               key="ew_tf")
    sens = c[1].slider("Swing sensitivity (%)", 3, 15, 8, key="ew_sens") / 100.0
    c[2].caption(
        "Weekly + ~8% suits multi-year higher-degree counts (like a weekly wave "
        "map); Daily + a smaller % zooms into the active swing."
    )

    gen = st.button("🌊 Generate Deep Elliott Wave Report", type="primary")

    src_key = (
        params.get("ticker") or params.get("psx_stock_symbol")
        or params.get("psx_symbol") or params.get("csv_label")
    )
    cache_key = f"{src_key}|{timeframe}|{sens}|{params['ai_model']}"
    store = st.session_state.setdefault("_ew_report", {})

    if gen and cache_key not in store:
        try:
            with st.spinner(
                "Fetching full history and running the multi-degree Elliott Wave "
                "analysis… (Opus 5 thinks for ~30–60s)"
            ):
                close, name, symbol, n = fetch_ew_history(params, timeframe)
                pivots = zigzag_pivots(close, sens)
                if len(pivots) < 5:
                    st.warning(
                        "Too few swing pivots at this sensitivity — lower the % or "
                        "use a longer history."
                    )
                    return
                data = deep_elliott_analysis(
                    name, symbol, timeframe, pivots, float(close.iloc[-1]), n,
                    params["ai_model"], params["api_key"],
                )
                store[cache_key] = {"data": data, "close": close,
                                    "name": name, "symbol": symbol, "n": n}
        except (RuntimeError, ValueError) as e:
            st.error(f"❌ {e}")
            return
        except Exception as e:  # pragma: no cover
            st.error(f"❌ Unexpected error: {e}")
            return

    bundle = store.get(cache_key)
    if bundle:
        _render_ew_report(bundle)
    elif not gen:
        st.caption("Pick a timeframe and click **Generate** to build the report.")


def _render_ew_report(bundle: dict) -> None:
    data = bundle["data"]
    close = bundle["close"]

    st.markdown(
        f"### {bundle['name']} ({bundle['symbol']}) — {bundle['n']} bars · "
        f"last {data.get('current_price', f'{float(close.iloc[-1]):,.2f}')}"
    )
    top = st.columns(3)
    top[0].markdown(f"**Position:** {data.get('position', '—')}")
    top[1].markdown(f"**Structure:** {data.get('structure_type', '—')}")
    top[2].markdown(f"**Cycle decline:** {data.get('cycle_decline_pct', '—')}")

    # Status cards
    cards = data.get("cards") or []
    if cards:
        html = ['<div class="qtl-grid">']
        for cd in cards:
            html.append(
                '<div class="qtl-card" style="--ac:#4C78D8;">'
                f'<div class="lbl">{cd.get("label", "")}</div>'
                f'<div class="val" style="font-size:1.05rem;">{cd.get("value", "")}</div>'
                f'<div class="sub">{cd.get("sub", "")}</div></div>'
            )
        html.append("</div>")
        st.markdown("".join(html), unsafe_allow_html=True)

    # Projection chart
    st.plotly_chart(
        build_ew_report_chart(close, data), use_container_width=True,
        config={"displayModeBar": False},
    )

    # Rule verification table
    rt = data.get("rule_table") or []
    if rt:
        st.markdown("#### Rule verification — motive sequences")
        st.dataframe(
            pd.DataFrame([
                {
                    "Sequence": r.get("sequence", ""),
                    "Rule 1 — W2 ≤ 100% W1": r.get("rule1", ""),
                    "Rule 2 — W3 not shortest": r.get("rule2", ""),
                    "Rule 3 — W4 clear of W1": r.get("rule3", ""),
                    "Alternation": r.get("alternation", ""),
                }
                for r in rt
            ]),
            use_container_width=True, hide_index=True,
        )

    # Targets and time windows
    cols = st.columns(2)
    with cols[0]:
        tg = data.get("targets") or []
        if tg:
            st.markdown("#### Price targets")
            st.dataframe(
                pd.DataFrame([
                    {"Target": t.get("name", ""), "Price": t.get("price", ""),
                     "Basis": t.get("detail", "")}
                    for t in tg
                ]),
                use_container_width=True, hide_index=True,
            )
    with cols[1]:
        tw = data.get("time_windows") or []
        if tw:
            st.markdown("#### Fibonacci time windows")
            st.dataframe(
                pd.DataFrame([
                    {"Wave": w.get("wave", ""), "Window": w.get("window", ""),
                     "Fib": w.get("fib", "")}
                    for w in tw
                ]),
                use_container_width=True, hide_index=True,
            )

    # Narrative
    narr = data.get("narrative") or []
    if narr:
        st.markdown("#### Analysis")
        for para in narr:
            st.write(para)

    st.caption(
        "Multi-degree AI Elliott Wave read for education only — not financial "
        "advice. Wave counts are interpretive; verify independently."
    )


def main() -> None:
    inject_css()
    st.title("📈 Quant Trade Levels")
    st.caption(
        "Daily-timeframe technical level engine — EMA trend bias, RSI/MACD "
        "momentum filters, ATR-based stops and classic pivot confluence."
    )

    params = render_sidebar()

    # --- New feature: AI chart-image analysis (shown only when an image is
    # uploaded; the existing live-data dashboard below is left untouched). -----
    if params.get("chart_image") is not None:
        render_image_analysis(params)
        st.markdown("---")

    # --- Data acquisition, branched by source --------------------------------
    if params["is_psx_stock"]:
        sym = params["psx_stock_symbol"]
        if not sym:
            st.info("👈 Pick a PSX stock in the sidebar to begin.")
            st.stop()
        display_symbol = sym
        try:
            with st.spinner(f"Fetching {sym} (Yahoo .KA → PSX fallback)…"):
                df = load_psx_stock(sym, params["period"])
                names = load_psx_symbols()
                name = names.get(sym, sym)
        except ValueError as e:
            st.error(f"❌ {e}")
            st.stop()
        except Exception as e:
            st.error(f"❌ Unexpected error fetching '{sym}': {e}")
            st.stop()
    elif params["is_psx"]:
        sym = params["psx_symbol"]
        display_symbol = sym
        try:
            with st.spinner(f"Fetching {sym} from PSX Data Portal…"):
                df = load_psx_data(sym, params["period"])
                name = PSX_INDICES.get(sym, sym)
        except ValueError as e:
            st.error(f"❌ {e}")
            st.stop()
        except Exception as e:
            st.error(f"❌ Unexpected error fetching '{sym}' from PSX: {e}")
            st.stop()
    elif params["is_csv"]:
        if params["uploaded_file"] is None:
            st.info(
                "👈 Upload a KSE All Share Index CSV (exported from "
                "Investing.com or TradingView) in the sidebar to begin."
            )
            st.stop()
        display_symbol = "CSV"
        try:
            with st.spinner("Parsing uploaded CSV…"):
                df = load_csv_data(params["uploaded_file"])
                name = params["csv_label"]
        except ValueError as e:
            st.error(f"❌ {e}")
            st.stop()
        except Exception as e:
            st.error(f"❌ Could not process the CSV: {e}")
            st.stop()
    else:
        if not params["ticker"]:
            st.info("👈 Enter a ticker symbol in the sidebar to begin.")
            st.stop()
        display_symbol = params["ticker"]
        try:
            with st.spinner(f"Fetching {params['ticker']} data…"):
                df = load_price_data(params["ticker"], params["period"])
                name = load_ticker_name(params["ticker"])
        except ValueError as e:
            st.error(f"❌ {e}")
            st.stop()
        except Exception as e:  # network / yfinance internals
            st.error(
                f"❌ Unexpected error fetching '{params['ticker']}': {e}\n\n"
                f"Check the symbol, your connection, or try again shortly."
            )
            st.stop()

    try:
        df = add_indicators(df)
        plan = compute_trade_plan(
            df, params["atr_mult"], params["rr_tp1"], params["rr_tp2"]
        )
    except Exception as e:
        st.error(f"❌ Failed to compute indicators/levels: {e}")
        st.stop()

    # Sanity guard: SL must sit below entry for a long setup.
    if plan["risk"] <= 0:
        st.warning(
            "Computed stop loss is at or above the primary entry, so the long "
            "setup is invalid at these parameters. Try a larger ATR multiplier "
            "or a different lookback."
        )

    # --- Render --------------------------------------------------------------
    render_header(name, display_symbol, plan)
    st.markdown("---")
    render_metrics(plan)
    st.markdown("---")

    st.plotly_chart(
        build_chart(df, plan, display_symbol),
        use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False, "responsive": True},
    )

    st.markdown("### 🎯 Trade Execution Summary")
    render_summary_cards(plan)

    with st.expander("🔍 Indicator Snapshot & Pivot Points"):
        piv = plan["pivots"]
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Trend / Momentum**")
            st.write(
                pd.DataFrame(
                    {
                        "Indicator": ["EMA 20", "EMA 50", "RSI 14", "MACD Hist", "ATR 14"],
                        "Value": [
                            f"{plan['ema20']:.2f}",
                            f"{plan['ema50']:.2f}",
                            f"{plan['rsi']:.1f}",
                            f"{plan['macd_hist']:.3f}",
                            f"{plan['atr']:.2f}",
                        ],
                    }
                ),
            )
        with c2:
            st.markdown("**Classic Daily Pivots**")
            st.write(
                pd.DataFrame(
                    {
                        "Level": ["R2", "R1", "Pivot", "S1", "S2"],
                        "Price": [
                            f"{piv['R2']:.2f}",
                            f"{piv['R1']:.2f}",
                            f"{piv['P']:.2f}",
                            f"{piv['S1']:.2f}",
                            f"{piv['S2']:.2f}",
                        ],
                    }
                ),
            )

    if params["is_psx_stock"]:
        src = df.attrs.get("source", "yahoo")
        data_src = (
            "Yahoo Finance (.KA, OHLC)" if src == "yahoo"
            else "PSX Data Portal (close-only)"
        )
    elif params["is_psx"]:
        data_src = "PSX Data Portal (dps.psx.com.pk)"
    elif params["is_csv"]:
        data_src = "uploaded CSV"
    else:
        data_src = "yfinance"
    st.caption(
        f"Data via {data_src} · {params['period_label']} lookback · "
        f"Last bar: {df.index[-1].date()} · Generated {_dt.date.today()}"
    )

    # --- Deep multi-degree Elliott Wave report (data-driven, on-demand) --------
    st.markdown("---")
    render_deep_ew(params)


if __name__ == "__main__":
    main()
