# Liquidity Sweep Model [LSM] 1H/5M — Pine Script v6

`LSM_1H_5M.pine` is a TradingView **indicator** (Phase 1) that automates the rule-based
liquidity-sweep model: 1H bias → location → sweep candle (C1) grading → second-sweep risk →
displacement / 5M MSS → entry models E1–E5 → stops, sizing, targets → virtual trade management,
with a dashboard, alerts and Pine Screener outputs.

Install: TradingView → Pine Editor → paste the file → *Add to chart* on a **5-minute** chart.
Choose the **Market preset** (`US` / `PSX` / `Custom`) and set `lotSize` for PSX board lots.

## State machine (per direction, one live setup each)

```
IDLE ─ sweep of an unconsumed pool (pre-gates: 1H bias/CHoCH, discount/premium, session, rank) ─► C1_DETECTED
C1_DETECTED ─ risk HIGH ─► WATCH_SECOND_SWEEP            (risk HIGH has priority over grade)
            ─ grade C ───► WAIT_RECLAIM ─(reclaim ≤ reclaimBars, then MSS ≤ mssWindow)─► MSS_CONFIRMED | CANCELLED
            ─ grade A/B ─► WAIT_MSS ─(MSS ≤ mssWindow)─► MSS_CONFIRMED | timeout → risk≥MOD ? WATCH_SECOND_SWEEP : CANCELLED
   pre-MSS: acceptBars closes beyond the level → CANCELLED · C1 extreme taken → WATCH_SECOND_SWEEP
MSS_CONFIRMED (waits ≤ fvgWaitBars for the displacement FVG)
            ─ WEAK displacement or risk HIGH ─► WATCH_SECOND_SWEEP
            ─ gates + score ─► ARMED (E1 limit) / IN_TRADE (E2 tier-1, E3, E5 fill at close) | SKIPPED
WATCH_SECOND_SWEEP ─(bounce, C1 low retaken, close back, 2nd MSS ≤ secondSweepBars)─► E5 | CANCELLED | INVALID (3rd sweep)
ARMED ─ limit touched ─► IN_TRADE ─ TP1 (partial + BE) ─► TRAIL ─► DONE (TP2 / runner) | STOPPED | SCRATCHED
      ─ FVG left unfilled + price runs + consolidation / breakaway FVG ─► E3
STOPPED (1st time, fresh low just below C1) ─► REENTRY_WATCH ─(reclaim + MSS)─► E5 re-entry, half risk (max 1)
any active state ─ 3rd sweep / 1H close beyond swept zone / 1H BOS against ─► INVALID (direction blocked for the day)
last regular bar of the session (flatEOD) ─► open trade flattened, pending setups cancelled
```

State codes (data window / screener): 0 IDLE · 1 C1 · 2 WAIT_RECLAIM · 3 WAIT_MSS · 4 MSS_CONFIRMED ·
5 WATCH_SECOND_SWEEP · 6 ARMED · 7 IN_TRADE · 8 TRAIL · 9 DONE · 10 STOPPED · 11 SCRATCHED ·
12 CANCELLED · 13 SKIPPED · 14 INVALID · 15 REENTRY_WATCH.

## Ambiguous rules and the interpretation used

| # | Rule | Ambiguity | Interpretation implemented |
|---|------|-----------|---------------------------|
| 1 | Counter-trend only after a 1H CHoCH | Bias = last BOS, so a CHoCH that is a BOS would already flip the bias | CHoCH = 1H close beyond a **minor** swing (`htfChochLen`=1) against the current bias, before any new structural BOS. Needs `allowReversals`. |
| 2 | Rank 4 "not tradeable alone" vs risk factor "swept rank 3–4 only" | Can rank-4 sweeps ever trade? | Rank-4-only sweeps are rejected **unless** C1 also taps an untapped 1H FVG/OB (`allowRank4Zone`). |
| 3 | E1 for MODERATE displacement | Step 6 says CE (50%); decision table says top edge | Input `e1ModEdge`, default **CE**. STRONG always uses the top edge. |
| 4 | Auto stop priority | A-grade + LOW risk + E2 STRONG matches two rules | Structural (risk ≥ MOD or deeper pool) → Sweep low (grade A) → Aggressive (E2/E3 + STRONG) → Sweep low. A flagged deeper pool escalates **any** mode to Structural. |
| 5 | MODERATE risk: "starter on C1, main size for E5" | Two entries in one setup | Starter = E1 at `starterPct` of size. While it is open, the second-sweep engine runs and an E5 reclaim **adds** the remaining size (stop tightened to below the 2nd sweep low). |
| 6 | E5 "fresh or equal low" | The bar right after C1 is almost always within `eqTol` | A second sweep needs ≥ 2 bars after C1 **and** a bounce ≥ `minBounceATR`×ATR. Deeper lows without a bounce extend the sequence instead. |
| 7 | "≥ 50% of the distance to TP1 covered" | Distance from where? Applies to limits? | Measured from the sweep low to TP1 at signal time. Blocks market entries (E2 tier-1, E3, E5); shown only as a flag for E1 limits. |
| 8 | Score point "second-sweep risk LOW" on E5 | E5 exists precisely because risk was not LOW | E5 entries receive the point (the 2nd sweep resolved the risk). |
| 9 | "Acceptance = bodies close beyond" | Full body or close? | The close beyond the level counts. |
| 10 | 1H "body close beyond the swept zone" (invalidation) | Beyond the level or beyond the sweep low? | Beyond the far edge of the sweep sequence (C1 / 2nd-sweep extreme). A 1H close back below the level only sets "1H confirm: no". |
| 11 | Displacement strength | Only STRONG/WEAK are specified precisely | STRONG = structural MSS + displacement candle with FVG + prime window. MODERATE = structural MSS + any FVG. WEAK = internal MSS or no FVG. |
| 12 | Aggressive stop "displacement low **or** FVG bottom" | Which one? | Displacement-candle low (the safer of the two); FVG bottom if no displacement candle. |
| 13 | Structural stop when nothing is deeper | No reference level | C1 sequence extreme − buffer − `structPadATR`×ATR. |
| 14 | E2 tier-1 "at the MSS candle close" when the FVG is confirmed a bar later | Back-dated fill | Tier-1 fills at the close of the bar where the plan is finalised (= MSS close if same bar). No back-dated fills. |
| 15 | Fresh 5M BOS for BE; lower high for time stop | Which swings? | Internal pivots (`ltfIntPivotLen`) confirmed after the fill. |
| 16 | R reporting | Starters, tiers, re-entries | R is measured against the setup's **full planned** risk budget (a starter-only trade shows ≈ 0.33×). Re-entry fills use 0.5 budget. |
| 17 | Session pools on regular-hours charts | Asia/London have no bars on RTH charts | They only appear with extended-hours data. "Prior session" H/L (`SESS H/L`) is pushed when the main session ends (time-gap heuristic). |

Gates are applied at two points. Bias, location, rank, session and the per-day invalidation block are checked
**at C1**, so rejected sweeps don't create noise; turn on *Show rejected sweeps* to see them. All gates,
plus R:R, news, midday, chase and score, are checked again at **ARMED** time.

## Test checklist — 6 chart scenarios

Run on a liquid US stock (e.g. SPY/AAPL, 5m, preset US) and on a PSX name (preset PSX, `lotSize` 500).
Use bar replay to step through each scenario.

| # | Scenario | What to look for |
|---|----------|------------------|
| 1 | **Clean A-grade with FVG retest** — 1H BULL bias, price in discount sweeps PDL or an EQL with a green candle closing in its top third, strong displacement up during the prime window that leaves a 5M FVG, price retraces into the FVG | Green `C1 A · PDL` label with `2nd-sweep risk: LOW`; `MSS` line from the leg origin; purple 5M FVG box; **E2a/E2b** (STRONG) or **E1** (MODERATE) lines with ★, red SL line `−1R`, green TP1/TP2 with R. Dashboard: ARMED → IN_TRADE on fill, score ≥ 4/7, R:R ≥ 2. Alerts: C1 → MSS → ARMED → Entry → TP1 → Move SL to BE. |
| 2 | **A-grade, FVG never retested** — same as 1 but price runs away and then pauses in a tight 2–4 bar range | E1 limit stays unfilled (state ARMED). When price closes above the consolidation (or prints a breakaway FVG), an `E3 cons` box and an **E3** entry line appear; state goes straight to IN_TRADE with the stop under the consolidation low. If R:R fails, E3 is ignored and E1 expires after `armedExpiry` bars ("Limit not filled"). |
| 3 | **Second sweep → E5** — C1 near the open, or with a deeper PDL / untapped 1H FVG within 1 ATR under it (risk HIGH), price bounces, then takes the C1 low again, closes back above it and breaks the bounce high | C1 badge `2nd-sweep risk: HIGH`, state WATCH_SECOND_SWEEP (no entry on C1). On the reclaim and break: `MSS` line, **E5** entry at the close, SL just below the second-sweep low, alerts "E5 second-sweep reclaim" + "Entry triggered". A further low below the 2nd sweep low → INVALID ("third sweep"). |
| 4 | **C-grade reclaim** — C1 is red and closes in its bottom third (warning text under the label), the next 1–3 bars close above the midpoint of C1's body and then break the leg origin | Red `C1 C` label with ⚠ reasons; state WAIT_RECLAIM → alert "C-grade reclaim" → MSS → plan. The stop anchors to the lowest low of the C1 sequence. Without a reclaim within `reclaimBars`: CANCELLED "C grade: no reclaim". |
| 5 | **C-grade acceptance → cancelled** — after C1, two consecutive 5M closes back below the swept level | State CANCELLED "Acceptance beyond <level> → breakout"; the C1 label greys out; the level is faded (consumed) and is never reused. |
| 6 | **Time-stop scratch** — the trade fills, but TP1 isn't reached within 7 bars and a lower internal 5M high prints | Alert "Time stop — scratch", state SCRATCHED, result `Time stop — scratch ±x.xR`, plan lines trimmed at that bar. |

Also check: on a non-5m chart a red warning label appears and nothing fires (unless the override is on).
Light and dark themes both stay readable, because the dashboard uses `chart.fg_color` / `chart.bg_color`.
The data window shows `LSM state code / score / direction / grade / risk / R:R / entry / stop / TP1 / signal`.

## Alerts

* **`alert()` (recommended):** create one alert on the indicator with *Any alert() function call*.
  Each event sends a JSON payload:
  `{"ticker","tf","dir","event","state","grade","risk","score","model","entry","entry2","stop","tp1","tp2","rr","qty","realizedR"}`.
  Each event has its own toggle in the **Alerts** input group.
* **`alertcondition()`:** separate static conditions for C1, reclaim, MSS, ARMED, entry, E5, TP1, BE,
  time stop, re-entry and invalidation.

## Phase 2 — `LSM_1H_5M_Strategy.pine` (backtest companion)

The strategy is generated from the indicator: the engine (inputs, state machine, gates, grading,
sizing, virtual trade management) is **identical**, so a backtest exercises exactly what the
indicator shows. On top of the engine it adds three things.

**1. Order routing to the Strategy Tester** (`Route orders` input)

| Engine event | Order |
|---|---|
| E1 / starter ARMED | `strategy.entry` **limit** at the E1 price, re-issued every bar until filled, expired or cancelled |
| E2 tier-1, E3, E5, E5 re-entry | **market** entry on the signal close (`process_orders_on_close = true`) |
| E2 tier-2 | limit at the FVG top edge while TP1 has not been hit |
| E5 add (MODERATE-risk starter) | market add of the reserved main size |
| TP1 / TP2 / runner | `strategy.exit` with `tp1Pct` → TP2 (half of the rest if a runner exists) → runner; every exit carries the engine's current stop, so BE moves and trailing are mirrored |
| Time stop, invalidation, EOD flat, engine/emulator mismatch | `strategy.close_all` |

Sizing uses `strategy.equity × riskPct` by default (or the fixed amount / account size), multiplied by the
score tier, starter, midday and re-entry factors, and rounded down to `lotSize`.
A strategy cannot be long and short at the same time, so the strategy adds one extra gate:
**no new setup is armed while the opposite direction is ARMED or in a trade.** Apart from this gate,
the strategy fires the same signals as the indicator.

**2. R ledger.** Every completed virtual trade is recorded with its **realised R**, measured against the
planned risk budget (partials, BE and trail included, so an 0.33× starter shows as 0.33× R). Trades are
bucketed by:

* entry model — E1, E2, E3, E4 (reserved; E4 is shown as an alternative, not traded), E5
* C1 grade — A / B / C
* second-sweep risk — LOW / MOD / HIGH, as it stood at entry
* session window at fill — Opening, Prime, Midday, Afternoon, Late
* direction — long / short

The table shows n, win %, average R, total R and profit factor (in R) for each bucket, plus max drawdown
in R, the tester's net P&L, and a log of the last N trades. Each closed trade also gets an R label on
the chart. Data-window series (`trade R`, `cumulative R`, `trades counted`) can be exported with
*Export chart data* for spreadsheet work. Use **Count trades from** to split in-sample and
out-of-sample periods.

**3. How to tune.** Use the R ledger, not the tester's P&L. The ledger is deterministic and matches the
indicator. The broker emulator can differ slightly: intrabar fill order, gaps through stops, lot rounding,
and qty = 0 when one lot exceeds the risk budget (the ledger still records the trade). Typical loop:

1. Run a symbol over its full 5m history. Read the buckets: does E5 beat C1 entries when risk is HIGH?
   Is grade C worth trading? Is the prime window really better?
2. Change one threshold (e.g. `dispBodyMult`, `deepZoneATR`, `minRR`, `timeStopBars`) and compare total R,
   average R and PF on the same date range.
3. Confirm on the out-of-sample range by moving **Count trades from**.

Notes:

* `alertcondition()` is not allowed in strategies. `alert()` JSON events still fire.
* Set *Properties → Initial capital* to match your account when sizing from equity.

**Keeping the two files in sync.** The strategy is generated, so don't edit it by hand. Change the
indicator, then run the command below from the repo root. `pine/scripts/make_strategy.py` applies
asserted edits to the indicator: the strategy() header, backtest inputs, ledger fields, sizing from
equity and the opposite-position gate. It then appends `pine/scripts/strategy_tail.pine` (order
routing + statistics). Every edit must match exactly once, so if the indicator drifts, the script
stops with an error instead of producing a silently broken strategy.

```bash
python3 pine/scripts/make_strategy.py
```
