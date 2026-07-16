  ...book_20260701.jsonl -> 78 windows so far
  ...book_20260702.jsonl -> 357 windows so far
  ...book_20260703.jsonl -> 642 windows so far
  ...book_20260704.jsonl -> 930 windows so far
  ...book_20260705.jsonl -> 1201 windows so far
  ...book_20260706.jsonl -> 1290 windows so far
  ...book_20260707.jsonl -> 1464 windows so far
  ...book_20260708.jsonl -> 1635 windows so far
  ...book_20260709.jsonl -> 1898 windows so far
  ...book_20260710.jsonl -> 2043 windows so far
  ...book_20260711.jsonl -> 2179 windows so far
  ...book_20260712.jsonl -> 2272 windows so far
  ...book_20260713.jsonl -> 2545 windows so far
================================================================================================
CASE AUDIT — production-faithful clock-only merge-maker (OFFLINE sim on recorded tapes, no live)
================================================================================================
HEAD commit: (unknown — not a git checkout)
FROZEN config: chop_dev_thresh=9.9, revoke_mode=hard, freeze_sec=45.0, chop_lookback_sec=60.0, hard_cap=True | cap=6.0 size=5.0 link_margin=0.01 (function defaults = live values, asserted)
Live-faithful knobs: hard_cap=True, naked_cap=6, size=5, NO early phase, completion NEAR-END
  only, clock-only (chop_trend_revoke=False via dev=9.9), freeze_sec=45, replace_shift=0.02,
  dwell=4, link_margin=0.01.
NOTE: the sim does NOT model FOK-kill. A rode_* (D/E) case means the near-end price CONDITION
  was never met (no bid to sell into / pair>=$1 with no completable light leg / budget out),
  NOT a FOK that was tried and killed. Live FOK-kill is a separate, unmodelled tail.
Total windows audited: 2545  traded: 2517  no-entry(F): 28

------------------------------------------------------------------------------------------------
1. REGIME x CASE matrix (rows: hindsight regime; cols: exit case)
------------------------------------------------------------------------------------------------
   each cell: n | %%row | meanPnL | tot$ | mean naked@frz | mean pair_eff
regime     | case A                             | case B                             | case C                             | case D                             | case E                            
chop       | 131   10%  +1.76  +230.9  0.5 0.9220  | 188   14%  +2.84  +533.7  3.1 0.8755  | 852   65%  +0.58  +492.0  3.2 0.9200  | 22    2%  +3.66   +80.6  3.6 0.9245! | 127   10%  +0.09   +11.7  3.2 0.9287    (rowN=1320)
reversal   | 20    3%  +1.73   +34.7  0.6 0.9245! | 42    6%  +2.52  +106.0  3.0 0.8845  | 435   66%  +0.07   +29.8  3.4 0.9101  |  1    0%  +4.24    +4.2  3.7 0.9293! | 164   25%  -0.11   -18.2  3.6 0.9199    (rowN=662)
trend      | 22    4%  +1.39   +30.6  0.5 0.9366! | 22    4%  +1.73   +38.1  2.4 0.9174! | 354   66%  -0.27   -97.2  3.7 0.9064  |  2    0%  +2.21    +4.4  1.8 0.9252! | 135   25%  -0.26   -35.7  3.6 0.9166    (rowN=535)
F          no-entry (gate rejected): n=28  PnL=$0
chop(hindsight) GO/NO-GO subset: n=1320  n(pair_eff)=1290  mean pair_eff=0.9146
  NOTE: live's causal-chop is measured by detector=='chop'; clock-only run has no detector
  trend, so ALL traded windows are 'acted-chop'. This subset approximates the go/no-go cohort
  by HINDSIGHT-chop (post-hoc), not the live detector label.

------------------------------------------------------------------------------------------------
2. NAKED-AT-FREEZE deep-dive (traded windows arriving at freeze with naked >= 1)
------------------------------------------------------------------------------------------------
(a) share of TRADED windows with naked@freeze >= 1:
    overall: 2344 / 2517 = 93.1%
    chop     : 1189 / 1320 = 90.1%
    reversal : 642 / 662 = 97.0%
    trend    : 513 / 535 = 95.9%
(b) of those naked>=1, branch mix (B/C/D/E) per regime:
    chop      (n=1189): B   16%  C   72%  D    2%  E   11%
    reversal  (n=642): B    7%  C   68%  D    0%  E   26%
    trend     (n=513): B    4%  C   69%  D    0%  E   26%
(c) conditional PnL of each branch per regime (mean / p50 / p90 / worst):
    chop      B (n=188): mean  +2.84  p50  +2.51  p90  +4.66  worst  +0.58
    chop      C (n=852): mean  +0.58  p50  +0.65  p90  +2.01  worst  -3.19
    chop      D (n=22): mean  +3.66  p50  +4.18  p90  +4.75  worst  +1.40
    chop      E (n=127): mean  +0.09  p50  +0.13  p90  +1.65  worst  -2.77
    reversal  B (n=42): mean  +2.52  p50  +2.31  p90  +4.22  worst  +0.41
    reversal  C (n=435): mean  +0.07  p50  +0.10  p90  +1.78  worst  -3.57
    reversal  D (n= 1): mean  +4.24  p50  +4.24  p90  +4.24  worst  +4.24
    reversal  E (n=164): mean  -0.11  p50  -0.06  p90  +1.76  worst  -3.38
    trend     B (n=22): mean  +1.73  p50  +1.59  p90  +2.26  worst  +0.74
    trend     C (n=354): mean  -0.27  p50  -0.10  p90  +1.27  worst  -2.78
    trend     D (n= 2): mean  +2.21  p50  +2.21  p90  +2.98  worst  +1.25
    trend     E (n=135): mean  -0.26  p50  -0.03  p90  +1.04  worst  -2.97
(d) histogram of naked@freeze (traded windows), and cap invariant (must be <= 6):
    [0,1)             173    6.9%
    [1,2)             513   20.4%
    [2,3)             550   21.9%
    [3,4)             484   19.2%
    [4,5)             255   10.1%
    [5,6)             524   20.8%
    >=6 (VIOLATION)    18    0.7%
    max naked@freeze = 6.00
    OK: max naked@freeze <= 6 everywhere (hard_cap holds).
(e) INVARIANT — merged windows with max_pair_cost >= 1.00 (expected 0):
    max_pair_cost = TRANSIENT max of (avg_up + avg_dn) observed at any tick BEFORE a merge.
    realized pair_cost = merged_cost/merged = the actual per-pair cost the window booked.
    OK: 0 merged windows with max_pair_cost >= $1.00 (linked-pair cap holds).

------------------------------------------------------------------------------------------------
3. PER-CASE PnL percentiles ($/window)
------------------------------------------------------------------------------------------------
case       n      p05      p25      p50      p75      p95      min      max
A        173    +0.56    +1.12    +1.51    +2.14    +3.52    -0.14    +4.40
B        252    +0.85    +1.51    +2.33    +4.18    +4.98    +0.41    +6.75
C       1641    -2.22    -0.56    +0.34    +1.20    +2.22    -3.57    +6.72
D         25    +1.52    +2.84    +4.14    +4.29    +4.83    +1.25    +5.14
E        426    -2.37    -0.99    +0.01    +0.86    +1.92    -3.38    +3.60

------------------------------------------------------------------------------------------------
4. 10 WORST windows by full PnL
------------------------------------------------------------------------------------------------
slug                           day         regime    case  naked@frz    exit_branch        $
btc-updown-5m-1783266000       2026-07-05  reversal  C     5.86         sold           -3.57
btc-updown-5m-1782942000       2026-07-01  reversal  E     5.00         rode_lost      -3.38
btc-updown-5m-1783171500       2026-07-04  chop      C     5.00         sold           -3.19
btc-updown-5m-1783958700       2026-07-13  reversal  C     5.08         sold           -3.12
btc-updown-5m-1783146000       2026-07-04  chop      C     5.00         sold           -3.09
btc-updown-5m-1782952500       2026-07-02  chop      C     5.00         sold           -3.08
btc-updown-5m-1782951300       2026-07-02  reversal  C     4.74         sold           -3.06
btc-updown-5m-1783097700       2026-07-03  reversal  C     5.18         sold           -3.06
btc-updown-5m-1783985400       2026-07-13  reversal  C     5.00         sold           -3.00
btc-updown-5m-1783178400       2026-07-04  reversal  C     5.20         sold           -2.99

------------------------------------------------------------------------------------------------
5. pair_eff sigma for n-sizing (chop-hindsight subset)
------------------------------------------------------------------------------------------------
chop(hindsight) per-window pair_eff: n=1290  mean=0.9146  sigma(pop)=0.0951
APPROXIMATION: pairs within a window are correlated, so we approximate the per-PAIR sigma by
  the per-WINDOW pair_eff sigma (conservative). SE(mean) = sigma / sqrt(n).
    n= 50 pairs -> SE of mean pair_eff = 0.0135
    n= 80 pairs -> SE of mean pair_eff = 0.0106
    n=100 pairs -> SE of mean pair_eff = 0.0095

------------------------------------------------------------------------------------------------
6. RECONCILIATION (must balance to the cent)
------------------------------------------------------------------------------------------------
   case counts: F=28  A=173  B=252  C=1641  D=25  E=426
   Sigma(counts across F,A,B,C,D,E) = 2545   total windows = 2545   diff = 0
   Sigma($ across all cases) = +1445.66   aggregate PnL (all windows) = +1445.66   diff = +0.0000
   OK: reconciliation balances (counts exact, $ within $0.01).

------------------------------------------------------------------------------------------------
7. MEASURED case frequencies vs spec §4 (A~51% clean-ish, D/E trend tail ~20%)
------------------------------------------------------------------------------------------------
   measured (share of TRADED): A=6.9%  B=10.0%  C=65.2%  D=1.0%  E=16.9%
   spec: A~51%% clean-ish ; D+E trend tail ~20%%
   measured A=6.9% (spec ~51%) ; measured D+E=17.9% (spec ~20%)
   -> the DOC (spec), not the numbers, is corrected where they differ.

------------------------------------------------------------------------------------------------
8. MEASURED vs ASSUMED / reliability
------------------------------------------------------------------------------------------------
   Any regime x case cell with 0 < n < 30 is marked '!' in the matrix above -> unreliable (n<30).
   unreliable cells: chop/D(n=22), reversal/A(n=20), reversal/D(n=1), trend/A(n=22), trend/B(n=22), trend/D(n=2)
   MEASURED numbers above supersede any prior report/spec where they disagree (number wins).
================================================================================================
[report written to /home/ubuntu/poly-quoter/scripts/_case_audit_report.md]

