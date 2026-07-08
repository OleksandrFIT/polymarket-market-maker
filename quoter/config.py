"""Runtime configuration for poly-quoter.

Loaded once at startup from environment variables (.env file).
Frozen dataclass — no runtime mutation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Mode = Literal["shadow", "paper", "live"]


@dataclass(frozen=True)
class Config:
    """All runtime settings. Immutable after construction."""

    # ── Execution mode ──
    mode: Mode = "shadow"
    bankroll_usd: float = 100.0

    # ── Markets ──
    assets: tuple[str, ...] = ("BTC", "ETH")
    timeframes: tuple[str, ...] = ("5m",)  # 15m disabled: paper showed -$30 vs +$39 on 5m

    # ── Phase-19 two-sided merge-maker ──
    # Post maker bids on BOTH outcomes at a target pair cost < $1.00
    # (Up @ mid-δ, Down @ (1-mid)-δ, where δ = merge_edge/2); the matched
    # complementary pairs are merged to $1.00, locking the spread regardless of
    # direction. A balance gate caps naked (one-sided) exposure. Buy-only.
    merge_edge: float = 0.01             # target total edge per Up+Down pair (per-leg δ = /2)
    max_naked_shares: int = 20           # hard cap on |yes_qty - no_qty|
    merge_levels: int = 2                # bids per side per tick
    flat_size: int = 10                  # flat shares per bid
    per_market_cap_usd: float = 50.0     # $ ceiling on ACCUMULATED spend per market
    min_time_to_expiry_sec: float = 5.0  # below this → no quotes

    # phase-22 laddered re-quoter
    ladder_anchor: str = "entry"  # "entry" (static from entry-mid) | "book" (chase best bid)
    rungs: int = 5                # rungs per side
    rung_size: int = 5            # shares per rung (Polymarket min 5)
    rung_spacing: float = 0.03    # price step between rungs
    naked_cap: int = 10           # max |inv_yes - inv_no| → pull heavier side's rungs
    per_window_cap: float = 12.0  # $ ceiling on committed spend per window
    max_inflight_rungs: int = 99  # staged posting: max rungs resting per side at once
                                  # (99 = all = legacy; run_control sets 1 to bound sweep)
    min_buy_price: float = 0.0    # never buy a side below this price (0 = no floor). A deep
                                  # dip < floor = market says that side is the likely loser
                                  # (a falling knife) → don't catch it. Costs the deep-cheap edge.

    # deep-ladder MEASUREMENT mode: rest a STATIC deep ladder both sides in a narrow
    # cheap band (deep_top .. min_buy_price) to catch the loser's crash at the same
    # deep prices the guru fills at (~$0.07). Bypasses the near-mid slot logic; the
    # money bound is per_window_cap. Real $ risk per window ≈ per_window_cap. Used to
    # measure OUR real cheap-leg fill price live before committing to a full rebuild.
    deep_ladder: bool = False
    deep_top: float = 0.15        # highest rung price in deep mode (anchor for both sides)

    # phase-24 auto-flat (kill-naked): sell the naked excess once it persists, then
    # suppress that side for the window. Threshold reuses naked_cap.
    auto_flat: bool = False           # OFF by default; run_control enables for live
    flatten_grace_sec: float = 20.0   # naked must stand at cap this long before selling
                                      # (lets a choppy imbalance pair up first)
    complete_pairs: bool = False      # near-end COMPLETE(<$1)/SELL; never ride naked
    complete_gate_sec: float = 60.0   # act only in the last N seconds of the window
    complete_continuous: bool = False # complete THROUGHOUT the window in small steps (not
                                      # one late shot): cheaper favorite + more retry time +
                                      # continuously balanced. SELL still only near-end.
    complete_step: int = 10           # max shares per completion shot (small = reliable fills)
    sell_fallback: bool = True        # True (legacy): SELL the naked loser near-end if it can't
                                      # be paired. False (guru-style): NEVER sell — HOLD the cheap
                                      # residual to resolution. The guru's wallet shows 3000/3000
                                      # BUY, 0 SELL: he holds losers to $0 (cheap) and keeps the
                                      # reversal-lottery upside. Removes the FOK-in-no-bid loss path.
    inv_reconcile_grace_sec: float = 12.0   # phantom-kill grace; MUST exceed data-api feed lag

    # phase-23 Binance trend detector
    trend_enabled: bool = True
    trend_confidence: float = 0.35    # THE knob: suppress a side when its win-prob < this
    trend_buffer_sec: float = 60.0    # rolling price-buffer window (seconds)
    trend_vol_fallback: float = 30.0  # fallback $-vol of BTC over a 5m window if buffer thin
    trend_stale_sec: float = 10.0     # buffer newest entry older than this → NEUTRAL (fail-safe)
    trend_gate_sec: float = 90.0      # detector acts only in the last N sec of the window

    # ── phase-25 momentum tilt (directional favorite accumulation) ──
    # When trend_detector confirms a bias, TAKER-buy the favorite (side of the BTC
    # move) toward favorite_shares ≈ spent. The base ladder is NOT trend-suppressed
    # (it keeps a moderate loser leg = insurance). A circuit-breaker pauses the tilt
    # by rolling paper-EV/share (EV, not hit-rate: hit-rate is blind to entry price —
    # a favorite bought at 0.83 needs ~83% wins just to break even).
    tilt_enabled: bool = False
    tilt_cutoff_sec: float = 45.0     # no taker tilt in the last N sec of the window
    tilt_fee: float = 0.02           # taker cost estimate (fee + sizing buffer)
    tilt_max_price: float = 0.90     # don't chase the favorite above this ask
    tilt_frac: float = 0.65          # target favorite $ = tilt_frac * per_window_cap
    dry_run: bool = False            # log intended orders, place NONE (paper on live data)
    regime_window: int = 20          # rolling window of directional calls
    regime_min_samples: int = 12     # shadow-only warm-up until this many windows
    regime_min_ev: float = 0.01      # min paper tilt-EV/share to keep tilt enabled

    # ── phase-26 5m early-consistent-leader strategy (separate from tilt) ──
    strategy: str = "tilt"           # "tilt" (15m momentum) | "five_min" (5m early leader)
    lean: int = 3                    # leader:laggard share ratio when accumulating
    band_lo: float = 0.62            # leader price band at minute 2 (inclusive)
    band_hi: float = 0.78

    # ── phase-27 top-of-book MM strategy ──
    tb_size: float = 5.0          # shares per quote per side
    tb_naked_cap: float = 10.0    # hard skew: stop a side when inv[side]+size-inv[other] > cap
    tb_tick: float = 0.001        # price-improvement tick over best bid
    tb_merge_min: float = 5.0     # merge matched pairs once min(inv) >= this
    requote_sec: float = 2.0      # top_book re-quote cadence (read book + adjust); env REQUOTE_SEC
    # direction-neutral mode: trade EVERY window (regime_gate off), pair both legs early &
    # aggressively so merges neutralize direction (like the profitable competitor 0xb27b)
    tb_early_sec: float = 0.0     # first this-many sec of the window = "early" phase (0 = off)
    tb_early_size: float = 0.0    # quote size during the early phase (0 = use tb_size)
    # regime gate: pair-maker only enters CALM windows (skips trends; earns in chop)
    regime_gate: bool = False     # enable auto skip-trending-windows for top_book
    regime_max_move_usd: float = 25.0   # net BTC move over lookback above this = trend = skip
    regime_lookback_min: int = 5  # trailing 1m BTC closes to measure the net move over
    # linked-pair quoting: cap the light-side bid so a fill pairs against the held heavy leg
    # for < $1 by construction (fixes async-fill pairs >$1); 0 = off
    tb_link_margin: float = 0.0
    # near-end pair completion: buy the light leg to close a naked pair (zero naked residual)
    tb_complete: bool = False           # enable near-end pair completion for top_book
    tb_complete_gate_sec: float = 45.0  # act only in the last this-many sec of the window
    complete_budget: float = 0.0        # $ headroom ABOVE per_window_cap for completes (self-funding)
    tb_complete_continuous: bool = False  # complete profitable (<$1) naked ALL window, not just near-end
    tb_sell_naked: bool = False         # when pair >= $1 (trend), SELL the loser instead of riding

    # ── phase-28 momentum-take strategy (0xb27b decode: chase mover + merge floor) ──
    mom_lookback: float = 30.0       # sec of mid history for the causal momentum signal
    mom_threshold: float = 0.03      # mid move over lookback to trigger a chase
    mom_chase_max: float = 0.95      # never take the mover above this price
    mom_residual_cap: float = 5.0    # max net directional exposure (mover inv - fader inv)/window

    # ── Quoter loop (Phase-9 faster cycle) ──
    requote_min_interval_ms: int = 50   # was 100 → 2× faster cycle
    requote_on_mid_move_cents: int = 1

    # ── Paper-fill realism (Phase-9+) ──
    # The live CLOB has 5-15 maker bids at each price level, plus tier-1
    # makers (Bonereaper) holding queue positions 1-3 with sub-10ms latency
    # from us-east-1. Our laptop sits ~110ms away in Slovakia and starts
    # at the back of the queue. The naive paper model (100% fill on cross)
    # massively over-estimates our edge. Toggle realistic_mode ON to model
    # queue priority + probabilistic taker arrivals + latency penalty.
    paper_fill_realistic_mode: bool = True
    paper_queue_position: int = 8         # default seat (middle of queue)
    paper_taker_size_min: int = 5         # smallest simulated taker SELL
    paper_taker_size_max: int = 200       # largest simulated taker SELL
    paper_latency_ms: int = 110           # round-trip Slovakia → us-east-1
    paper_fill_prob_multiplier: float = 1.0  # global knob (1.0 = baseline)

    # ── Risk ──
    max_daily_loss_usd: float = 50.0
    max_inventory_skew_shares: int = 5000  # effectively disabled (was 500)
    max_market_position_usd: float = 500.0
    stop_after_consecutive_loss_days: int = 2

    # ── WS health ──
    ws_stale_timeout_sec: float = 30.0  # force reconnect if no book update in N sec
    ws_reconnect_backoff_max_sec: float = 30.0

    # ── Paths ──
    db_path: str = "state.db"
    log_path: str = "logs/quoter.log"
    log_level: str = "INFO"

    # ── Polymarket endpoints ──
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    polymarket_web: str = "https://polymarket.com"
    ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ws_user_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

    # ── Binance ──
    binance_ws_url: str = "wss://stream.binance.com:9443/stream"

    @classmethod
    def from_env(cls) -> Config:
        """Load from environment. Missing values use dataclass defaults."""
        mode_str = os.environ.get("MODE", "shadow").lower()
        if mode_str not in ("shadow", "paper", "live"):
            raise ValueError(f"MODE must be shadow|paper|live, got {mode_str!r}")
        return cls(
            mode=mode_str,  # type: ignore[arg-type]
            bankroll_usd=float(os.environ.get("BANKROLL", "100")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def is_shadow(self) -> bool:
        return self.mode == "shadow"

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"
