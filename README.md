# Quant Trade Levels

A Streamlit dashboard that computes short-term technical trade levels
(Primary Entry S1, Deep Entry S2, ATR Stop Loss, TP1, TP2) for any stock or
index, with EMA trend bias, RSI/MACD momentum filters and classic pivot points.

## Data sources
- **Yahoo Finance** — US/global tickers and PSX stocks (`SYMBOL.KA`).
- **PSX Stock (KSE All Share)** — pick any of ~756 PSX constituents; OHLC candles
  from Yahoo where available, otherwise the PSX Data Portal close-only feed.
- **PSX Index** — official KSE indices (All Share, KSE-100, KMI-30, ...) from
  `dps.psx.com.pk`.
- **Upload CSV** — any daily history exported from Investing.com or TradingView.

## AI Chart Image Analysis (optional)
Upload any chart screenshot (any timeframe) in the sidebar to get three tabs of
Claude-vision analysis: **Descriptive TA** (support/resistance/stop-loss),
**Position Sizing** (risk-managed size from your account & risk %), and a
rules-checked **Elliott Wave** count with a projected-path chart.

Requires an Anthropic API key — enter it in the sidebar, or set
`ANTHROPIC_API_KEY` in `.streamlit/secrets.toml` (local) or the Streamlit Cloud
Secrets manager. The key is never committed.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy
Deployable free on [Streamlit Community Cloud](https://share.streamlit.io):
point it at this repo and `app.py`.

> Educational tool only. Not financial advice.
