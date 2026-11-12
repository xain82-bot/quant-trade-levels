"""Regenerate pine/LSM_1H_5M_Strategy.pine from the indicator (run from the repo root).

The strategy = indicator + the asserted edits below + scripts/strategy_tail.pine.
Every replacement must match exactly once, so engine drift is caught loudly.
"""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "LSM_1H_5M.pine")
DST = os.path.join(HERE, "..", "LSM_1H_5M_Strategy.pine")
src = open(SRC).read()

def rep(old, new, count=1):
    global src
    n = src.count(old)
    assert n == count, f"expected {count} got {n}: {old[:70]!r}"
    src = src.replace(old, new)

# ── title / header
rep("//  Liquidity Sweep Model [LSM] 1H/5M — Pine Script v6 indicator (Phase 1)",
"""//  Liquidity Sweep Model [LSM] 1H/5M — Pine Script v6 STRATEGY (Phase 2 · backtest companion)
//
//  PHASE 2 NOTES
//  ─────────────
//  This file reuses the Phase-1 engine verbatim (same inputs, state machine, gates, grading,
//  sizing and virtual trade management). On top of it it adds:
//   • Order routing: the engine's decisions are mirrored with strategy.entry/exit/close
//     (E1/E2b as limit orders, E2a/E3/E5/E5-add as market orders on the signal close,
//     TP1/TP2/runner + moving stop as strategy.exit, time stop / invalidation / EOD as close).
//   • An "R ledger": every completed virtual trade is recorded with its realised R
//     (vs the planned risk budget) and bucketed by entry model, C1 grade, second-sweep
//     risk and session window → stats table (n, win %, avg R, total R, profit factor),
//     trade log, per-trade R labels, data-window series for export.
//   • One position at a time: a strategy cannot be long and short simultaneously, so a
//     setup is not armed while the opposite direction is ARMED / in a trade (gate "opposite").
//  The R ledger is computed by the engine (deterministic, identical to the indicator's
//  dashboard). The Strategy Tester P&L comes from TradingView's broker emulator and can
//  differ slightly (intrabar fill assumptions, gaps, lot rounding, qty = 0 when the account
//  is too small for one lot). Tune thresholds from the R ledger; use the tester for equity.""")
rep('indicator("Liquidity Sweep Model [LSM] 1H/5M", shorttitle = "LSM 1H/5M", overlay = true, max_boxes_count = 500, max_lines_count = 500, max_labels_count = 500, max_bars_back = 1000)',
    'strategy("Liquidity Sweep Model [LSM] 1H/5M · Strategy", shorttitle = "LSM 1H/5M S", overlay = true, initial_capital = 10000, default_qty_type = strategy.fixed, default_qty_value = 1, pyramiding = 3, process_orders_on_close = true, commission_type = strategy.commission.percent, commission_value = 0.0, slippage = 0, max_boxes_count = 500, max_lines_count = 500, max_labels_count = 500, max_bars_back = 1000)')
rep("//   • alertcondition() messages are static (Pine restriction); use alert() for full JSON.",
    "//   • Strategies cannot use alertcondition(); use alert() (Any alert() function call) for events.")

# ── inputs
rep('alExit            = input.bool(true, "Stopped / closed", group = GA)',
'''alExit            = input.bool(true, "Stopped / closed", group = GA)

string GB = "Phase 2 · Backtest & statistics"
placeOrders       = input.bool(true, "Route orders to the Strategy Tester", group = GB, tooltip = "Off = statistics only (R ledger), no broker-emulator orders.")
sizeFromEquity    = input.bool(true, "Size from strategy equity (else Account size)", group = GB, tooltip = "Risk amount = equity × riskPct. Set Properties → Initial capital to your account.")
showStats         = input.bool(true, "Show R statistics table", group = GB)
statsPos          = input.string("Bottom right", "Statistics position", options = ["Top right", "Top left", "Bottom right", "Bottom left", "Middle right"], group = GB)
logRows           = input.int(8, "Trade log rows", minval = 0, maxval = 20, group = GB)
showTradeLabels   = input.bool(true, "Label realised R on each closed trade", group = GB)
statsFrom         = input.time(timestamp("2000-01-01T00:00:00"), "Count trades from", group = GB, tooltip = "Only trades closed after this time enter the statistics (walk-forward splits).")''')

# ── setup fields
rep("    int           endBar       = 0\n",
"""    int           endBar       = 0
    float         rStart       = 0.0      // realizedR at the start of the current trade
    int           entryWin                // session window index at fill
    float         addQty       = 0.0      // E5 add quantity (starter trades)
    float         q1           = 0.0      // tier-1 / single-entry quantity
    float         q2           = 0.0      // E2 tier-2 quantity
""")

# ── stores
rep("var array<bool> blocked = array.from(false, false)\n",
"""var array<bool> blocked = array.from(false, false)
var array<bool> busy    = array.from(false, false)   // direction ARMED or in a trade (one position at a time)

// R ledger buckets: 0 all · 1–5 E1..E5 · 6–8 grade A/B/C · 9–11 risk LOW/MOD/HIGH ·
// 12–16 window Opening/Prime/Midday/Afternoon/Late · 17–18 long/short
int NB = 19
var array<float>  bN    = array.new<float>(NB, 0.0)
var array<float>  bWin  = array.new<float>(NB, 0.0)
var array<float>  bSum  = array.new<float>(NB, 0.0)
var array<float>  bGP   = array.new<float>(NB, 0.0)
var array<float>  bGL   = array.new<float>(NB, 0.0)
var array<float>  bPeak = array.from(0.0, 0.0)         // [peak cumulative R, max drawdown R]
var array<string> logTx = array.new<string>()
var array<float>  logR  = array.new<float>()
var array<float>  lastTradeR = array.from(0.0)
var array<int>    lastTradeBar = array.from(-1)
""")

# ── trade recorder + hook in f_exit
rep("f_avg(Setup s) =>",
"""f_riskAmt() =>
    useFixedRisk ? fixedRisk : (sizeFromEquity ? strategy.equity : accountSize) * riskPct / 100.0

f_winIdx() =>
    inOpen ? 0 : inPrime ? 1 : inMidday ? 2 : inAfter ? 3 : 4

f_winTxt(int w) =>
    w == 0 ? "Opening" : w == 1 ? "Prime" : w == 2 ? "Midday" : w == 3 ? "Afternoon" : w == 4 ? "Late" : "—"

f_bAdd(int i, float r) =>
    array.set(bN, i, array.get(bN, i) + 1)
    array.set(bSum, i, array.get(bSum, i) + r)
    if r > 0
        array.set(bWin, i, array.get(bWin, i) + 1)
        array.set(bGP, i, array.get(bGP, i) + r)
    else
        array.set(bGL, i, array.get(bGL, i) - r)
    true

// Record one completed virtual trade in the R ledger.
f_recordTrade(Setup s) =>
    float r = s.realizedR - s.rStart
    int   d = s.direction
    array.set(lastTradeR, 0, r)
    array.set(lastTradeBar, 0, bar_index)
    if time >= statsFrom
        f_bAdd(0, r)
        int m = nz(s.entryModel)
        if m >= 1 and m <= 5
            f_bAdd(m, r)
        if nz(s.grade) >= 1
            f_bAdd(6 + 3 - s.grade, r)
        if not na(s.risk)
            f_bAdd(9 + s.risk, r)
        if not na(s.entryWin)
            f_bAdd(12 + s.entryWin, r)
        f_bAdd(d == 1 ? 17 : 18, r)
        float cum = array.get(bSum, 0)
        array.set(bPeak, 0, math.max(array.get(bPeak, 0), cum))
        array.set(bPeak, 1, math.max(array.get(bPeak, 1), array.get(bPeak, 0) - cum))
    string tag = (d == 1 ? "L " : "S ") + f_modelShort(s.entryModel) + (s.starter ? "s" : "") + (s.e5Done and s.starter ? "+E5" : "") + (s.isReentry ? "re" : "") + " · " + f_gradeTxt(s.grade) + " · " + f_riskTxt(s.risk) + " · " + f_winTxt(s.entryWin)
    array.unshift(logTx, str.format("{0,date,MM-dd HH:mm}", time) + " " + tag)
    array.unshift(logR, r)
    if array.size(logTx) > 20
        array.pop(logTx)
        array.pop(logR)
    if showTradeLabels
        label.new(bar_index, d == 1 ? high : low, f_fmtR(r) + "\\n" + tag, style = d == 1 ? label.style_label_down : label.style_label_up, color = color.new(r > 0 ? colTarget : r < 0 ? colStop : colNeutral, 20), textcolor = color.white, size = size.tiny)
    true

f_avg(Setup s) =>""")
rep("""        s.posSh     := s.posSh - shx
        s.posCost   := s.posCost - shx * avg
    true""",
"""        s.posSh     := s.posSh - shx
        s.posCost   := s.posCost - shx * avg
        if frac >= 1.0
            f_recordTrade(s)
    true""")
rep("""    s.runHigh     := na
    true""",
"""    s.runHigh     := na
    s.rStart      := s.realizedR
    s.addQty      := 0.0
    s.entryWin    := na
    true""")

# ── f_plan: equity sizing, opposite gate, tier quantities, entry window
rep("""    if smult == 0
        why := why + "score " + str.tostring(sc) + " · "
    bool pass = why == \"\"""",
"""    if smult == 0
        why := why + "score " + str.tostring(sc) + " · "
    if array.get(busy, d == 1 ? 1 : 0)
        why := why + "opposite position open · "
    bool pass = why == \"\"""")
rep("""        float riskAmt = useFixedRisk ? fixedRisk : accountSize * riskPct / 100.0
        float mult""", """        float riskAmt = f_riskAmt()
        float mult""")
rep("""        s.qty := math.floor(rawQ / lotSize) * lotSize
""", """        s.qty := math.floor(rawQ / lotSize) * lotSize
        s.q1  := model == 2 ? math.floor(s.qty * tpf / lotSize) * lotSize : s.qty
        s.q2  := model == 2 ? s.qty - s.q1 : 0.0
""")
rep("""                s.fillBar   := bar_index
                s.tradeHigh := f_nH(d, 0)
                s.state     := ST_INTRADE""",
"""                s.fillBar   := bar_index
                s.entryWin  := f_winIdx()
                s.tradeHigh := f_nH(d, 0)
                s.state     := ST_INTRADE""")
rep("""        s.fillBar   := bar_index
        s.tradeHigh := nH0
        s.state     := ST_INTRADE""",
"""        s.fillBar   := bar_index
        s.entryWin  := f_winIdx()
        s.tradeHigh := nH0
        s.state     := ST_INTRADE""")
rep("""                float riskAmt = useFixedRisk ? fixedRisk : accountSize * riskPct / 100.0
                s.qty := s.qty + math.floor(riskAmt * (1 - starterPct / 100.0) / rk / lotSize) * lotSize""",
"""                s.addQty := math.floor(f_riskAmt() * (1 - starterPct / 100.0) / rk / lotSize) * lotSize
                s.qty    := s.qty + s.addQty""")

# ── keep "busy" flags current inside the per-direction loop
rep("""        if flatEOD and session.islastbar_regular
            f_eod(s)""",
"""        if flatEOD and session.islastbar_regular
            f_eod(s)
        array.set(busy, i, s.state == ST_ARMED or s.state == ST_INTRADE or s.state == ST_TRAIL)""")

# ── drop alertcondition block (not allowed in strategies)
start = src.index("f_has(string c) =>")
end = src.index('alertcondition(f_has("INVALID")')
end = src.index("\n", end) + 1
src = src[:start] + src[end:]
assert "alertcondition(f_has" not in src and "f_has(" not in src

src = src.rstrip("\n") + "\n" + open(os.path.join(HERE, "strategy_tail.pine")).read()
open(DST, "w").write(src)
print("ok")
