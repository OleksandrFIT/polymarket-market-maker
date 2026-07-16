# Execution baseline — E-subdivision + sell_recovery shadow (OFFLINE, recorded tapes)

commit: `unknown`

frozen config: `{'chop_dev_thresh': 9.9, 'revoke_mode': 'hard', 'freeze_sec': 45.0, 'chop_lookback_sec': 60.0, 'hard_cap': True}` (cap/size/link_margin at the function defaults 6/5/0.01 = the live values)

tapes (13): book_20260701.jsonl, book_20260702.jsonl, book_20260703.jsonl, book_20260704.jsonl, book_20260705.jsonl, book_20260706.jsonl, book_20260707.jsonl, book_20260708.jsonl, book_20260709.jsonl, book_20260710.jsonl, book_20260711.jsonl, book_20260712.jsonl, book_20260713.jsonl

Shadow sim on RECORDED tapes. No live trading, no orders, no config change. These are
BASELINES for a future live comparison — nothing here is tuned.

## (1) E-SUBDIVISION — why the near-end close never executed

Windows total: 2543.  E (rode to resolution): 457 (18.0% of all windows).

The sim does NOT model FOK-kill: it executes the near-end close whenever the price CONDITION
holds. Every reason below is therefore STRUCTURAL, not 'an order was tried and killed'.
Live FOK-kill is a separate, unmodelled tail (production logs sell_kills/complete_kills).

### count / %% of that regime's E / mean PnL

| regime | no_near_end_snap | no_bid_on_loser | budget | other | regime E total |
|---|---|---|---|---|---|
| chop | 3 / 2% / +0.46 | 124 / 83% / -0.09 | 23 / 15% / +3.11 | — | 150 |
| reversal | 1 / 1% / -0.42 | 165 / 98% / -0.24 | 2 / 1% / +4.04 | — | 168 |
| trend | 1 / 1% / +0.12 | 136 / 98% / -0.40 | 2 / 1% / +1.97 | — | 139 |
| **ALL** | 5 / 1% / +0.21 | 425 / 93% / -0.25 | 27 / 6% / +3.10 | — | 457 |

### per-reason: mean naked_at_freeze and mean LOSER price at freeze

(loser = the leg left naked; its price at freeze is mid_up/mid_dn_at_freeze for that side.
 `_mid` falls back to the single present side when the book is one-sided.)

| reason | n | %% of E | mean PnL | mean naked_at_freeze | mean loser px @freeze | n px |
|---|---|---|---|---|---|---|
| no_near_end_snap | 5 | 1.1% | +0.21 | 3.49 | — | 0 |
| no_bid_on_loser | 425 | 93.0% | -0.25 | 3.45 | 0.009 | 425 |
| budget | 27 | 5.9% | +3.10 | 3.62 | 0.765 | 27 |
| other | 0 | — | — | — | — | 0 |

### HEADLINE

**no_bid_on_loser = 425 / 457 E windows = 93.0% of E** (mean PnL -0.25) — structurally UNFIXABLE
post-hoc: the loser book has no bid, there is nothing left to sell into. This part of the E
tail can only be attacked UPSTREAM (accumulate less naked), never by more persistent execution.

The REST of E = 32 / 457 = 7.0% (mean PnL +2.65) — reachable in principle by execution.

## (2) SELL_RECOVERY SHADOW BASELINE (case C: exit_branch == "sold")

Sold windows: 1660.  Usable (sell_px and a positive ref mid at freeze): 1660.  Skipped: 0.

recovery = sell_px / mid_at_freeze(sold side).

| regime | n | mean recovery | p10 | p50 | p90 | mean sell_px | mean ref_mid |
|---|---|---|---|---|---|---|---|
| chop | 860 | 0.864 | 0.667 | 0.941 | 0.989 | 0.174 | 0.200 |
| reversal | 438 | 0.859 | 0.667 | 0.909 | 0.988 | 0.104 | 0.114 |
| trend | 362 | 0.854 | 0.667 | 0.857 | 0.980 | 0.065 | 0.073 |
| **ALL** | 1660 | 0.861 | 0.667 | 0.909 | 0.989 | 0.131 | 0.149 |

| naked_at_freeze | n | mean recovery | p10 | p50 | p90 | mean sell_px | mean ref_mid |
|---|---|---|---|---|---|---|---|
| [1,3) | 742 | 0.865 | 0.667 | 0.933 | 0.990 | 0.153 | 0.170 |
| [3,5) | 530 | 0.846 | 0.667 | 0.909 | 0.987 | 0.119 | 0.141 |
| [5,6] | 388 | 0.872 | 0.667 | 0.909 | 0.987 | 0.108 | 0.122 |
| **ALL** | 1660 | 0.861 | 0.667 | 0.909 | 0.989 | 0.131 | 0.149 |

### HEADLINE

- **chop**: the sell recovers **86.4%** of the leg's freeze-time value (n=860, median 94.1%).
- **reversal**: the sell recovers **85.9%** of the leg's freeze-time value (n=438, median 90.9%).
- **trend**: the sell recovers **85.4%** of the leg's freeze-time value (n=362, median 85.7%).
- **ALL**: **86.1%** (n=1660, median 90.9%).

**THIS IS AN UPPER BOUND.** The shadow ALWAYS executes the sell whenever a bid exists; live
can have the FOK killed, so the live `sell_px_avg` / `mid_at_freeze` recovery will be WORSE
than every number in this table. A live figure BELOW these is expected, not a regression;
a live figure at or above them would mean the shadow is mismeasuring.

