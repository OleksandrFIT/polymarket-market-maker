================================================================================================
CASE AUDIT — production-faithful clock-only merge-maker (OFFLINE sim on recorded tapes, no live)
================================================================================================
HEAD commit: 15d0fdd
FROZEN config: chop_dev_thresh=9.9, revoke_mode=hard, freeze_sec=45.0, chop_lookback_sec=60.0, hard_cap=True | cap=6.0 size=5.0 link_margin=0.01 (function defaults = live values, asserted)
Live-faithful knobs: hard_cap=True, naked_cap=6, size=5, NO early phase, completion NEAR-END
  only, clock-only (chop_trend_revoke=False via dev=9.9), freeze_sec=45, replace_shift=0.02,
  dwell=4, link_margin=0.01.
NOTE: the sim does NOT model FOK-kill. A rode_* (D/E) case means the near-end price CONDITION
  was never met (no bid to sell into / pair>=$1 with no completable light leg / budget out),
  NOT a FOK that was tried and killed. Live FOK-kill is a separate, unmodelled tail.
Total windows audited: 2532  traded: 2504  no-entry(F): 28

------------------------------------------------------------------------------------------------
1. REGIME x CASE matrix (rows: hindsight regime; cols: exit case)
------------------------------------------------------------------------------------------------
   each cell: n | %%row | meanPnL | tot$ | mean naked@frz | mean pair_eff
regime     | case A                             | case B                             | case C                             | case D                             | case E                            
chop       | 117    9%  +1.91  +223.9  0.4 0.9168  | 198   15%  +2.65  +524.9  2.9 0.8825  | 848   65%  +0.51  +429.7  3.3 0.9208  | 19    1%  +3.48   +66.2  3.4 0.9266! | 132   10%  +0.03    +3.8  3.3 0.9249    (rowN=1314)
reversal   | 27    4%  +1.50   +40.6  0.6 0.9323! | 49    7%  +2.44  +119.5  2.9 0.8879  | 416   63%  -0.09   -37.4  3.5 0.9202  |  4    1%  +3.63   +14.5  3.9 0.9422! | 162   25%  -0.20   -32.6  3.6 0.8934    (rowN=658)
trend      | 18    3%  +1.61   +28.9  0.5 0.9275! | 21    4%  +1.76   +37.0  2.3 0.9220! | 357   67%  -0.37  -131.0  3.8 0.9102  |  1    0%  +3.31    +3.3  3.8 0.9368! | 135   25%  -0.31   -42.2  3.6 0.8987    (rowN=532)
F          no-entry (gate rejected): n=28  PnL=$0
chop(hindsight) GO/NO-GO subset: n=1314  n(pair_eff)=1277  mean pair_eff=0.9150
  NOTE: live's causal-chop is measured by detector=='chop'; clock-only run has no detector
  trend, so ALL traded windows are 'acted-chop'. This subset approximates the go/no-go cohort
  by HINDSIGHT-chop (post-hoc), not the live detector label.

------------------------------------------------------------------------------------------------
2. NAKED-AT-FREEZE deep-dive (traded windows arriving at freeze with naked >= 1)
------------------------------------------------------------------------------------------------
(a) share of TRADED windows with naked@freeze >= 1:
    overall: 2342 / 2504 = 93.5%
    chop     : 1197 / 1314 = 91.1%
    reversal : 631 / 658 = 95.9%
    trend    : 514 / 532 = 96.6%
(b) of those naked>=1, branch mix (B/C/D/E) per regime:
    chop      (n=1197): B   17%  C   71%  D    2%  E   11%
    reversal  (n=631): B    8%  C   66%  D    1%  E   26%
    trend     (n=514): B    4%  C   69%  D    0%  E   26%
(c) conditional PnL of each branch per regime (mean / p50 / p90 / worst):
    chop      B (n=198): mean  +2.65  p50  +2.34  p90  +4.47  worst  +0.05
    chop      C (n=848): mean  +0.51  p50  +0.60  p90  +1.99  worst  -3.20
    chop      D (n=19): mean  +3.48  p50  +3.78  p90  +4.39  worst  +1.88
    chop      E (n=132): mean  +0.03  p50  +0.04  p90  +1.91  worst  -2.78
    reversal  B (n=49): mean  +2.44  p50  +2.23  p90  +4.22  worst  +0.44
    reversal  C (n=416): mean  -0.09  p50  -0.02  p90  +1.71  worst  -3.57
    reversal  D (n= 4): mean  +3.63  p50  +3.94  p90  +4.24  worst  +2.38
    reversal  E (n=162): mean  -0.20  p50  -0.10  p90  +1.64  worst  -3.39
    trend     B (n=21): mean  +1.76  p50  +1.52  p90  +3.09  worst  +0.74
    trend     C (n=357): mean  -0.37  p50  -0.22  p90  +1.20  worst  -2.78
    trend     D (n= 1): mean  +3.31  p50  +3.31  p90  +3.31  worst  +3.31
    trend     E (n=135): mean  -0.31  p50  -0.05  p90  +1.26  worst  -2.97
(d) histogram of naked@freeze (traded windows), and cap invariant (must be <= 6):
    [0,1)             162    6.5%
    [1,2)             503   20.1%
    [2,3)             520   20.8%
    [3,4)             432   17.3%
    [4,5)             275   11.0%
    [5,6)             597   23.8%
    >=6 (VIOLATION)    15    0.6%
    max naked@freeze = 6.00
    OK: max naked@freeze <= 6 everywhere (hard_cap holds).
(e) INVARIANT — merged windows with max_pair_cost >= 1.00 (expected 0):
    max_pair_cost = TRANSIENT max of (avg_up + avg_dn) observed at any tick BEFORE a merge.
    realized pair_cost = merged_cost/merged = the actual per-pair cost the window booked.
    STOP: linked-pair cap violated (pre-registered rule) — 25 merged window(s) had a
    transient max_pair_cost >= $1.00. Of these, 1 also booked a REALIZED pair_cost >= $1.00.
    NOTE: 'pairs' is total pairs merged in the window (fractional; a <1 value means the
    breach touched only a sub-unit merge).
    slug                             max_pairc   realiz_pc  pairs  comp  sell     pnl$
        btc-updown-5m-1783526100        1.0347      0.9115   23.0     1     0    +2.25
        btc-updown-5m-1783111800        1.0220      0.9575   20.0     0     1    +1.39
        btc-updown-5m-1783807500        1.0211      0.8397   25.0     1     0    +4.21
        btc-updown-5m-1783591500        1.0120      0.9409   17.9     0     1    -0.76
        btc-updown-5m-1783279500        1.0113      0.8693   21.7     0     1    +2.60
        btc-updown-5m-1782992400        1.0107      0.9786   20.0     0     1    -0.13
        btc-updown-5m-1783226100        1.0097      0.9585   17.2     0     1    -0.56
        btc-updown-5m-1783143000        1.0093      0.9896   20.0     0     1    +0.10
        btc-updown-5m-1783246200        1.0060      0.9640   21.7     0     1    +0.90
        btc-updown-5m-1783249200        1.0031      0.8986   20.0     0     1    +0.77
        btc-updown-5m-1782931800        1.0020      0.8376   25.0     0     0    +4.22
        btc-updown-5m-1782938700        1.0020      0.9661   18.4     0     0    +0.25
        btc-updown-5m-1783097400        1.0020      0.9859   12.8     0     1    -1.10
        btc-updown-5m-1783103700        1.0020      0.8422   24.2     0     1    +3.89
        btc-updown-5m-1783114800        1.0020      0.9446   21.3     0     0    +0.83
        btc-updown-5m-1783432800        1.0020      0.9458   20.0     0     1    +1.34
        btc-updown-5m-1783572000        1.0020      0.9455   20.0     0     1    +0.37
        btc-updown-5m-1783876800        1.0020      0.9842   10.4     0     1    -0.97
        btc-updown-5m-1783906500        1.0020      0.8687    6.0     0     0    -1.77
        btc-updown-5m-1783965600        1.0020      1.0020    0.0     0     0    -2.22
        btc-updown-5m-1783163100        1.0014      0.9464   22.1     0     0    +1.28
        btc-updown-5m-1783509300        1.0013      0.9543   20.0     0     1    +0.13
        btc-updown-5m-1783291200        1.0009      0.9561   19.8     0     0    -0.10
        btc-updown-5m-1783122600        1.0002      0.9316   22.1     0     0    +1.41
        btc-updown-5m-1783885200        1.0001      0.9748   17.3     1     0    +0.57
    CONTEXT (honest): the transient breach is a few marginal units whose blended per-side
    averages momentarily summed > $1; the linked light-cap only binds when TOPPING the
    SHORTER leg (inv[other]>inv[side]) and the near-end completion uses a 0-margin threshold
    (heavy_avg+lap<1.0), so both legs filled near their tops can transiently sum > $1.
    Whether this blocks live is a pre-registration call: by the LITERAL max_pair_cost rule it
    STOPS; by REALIZED pair_cost (1 window(s) >= $1) the economic loss is bounded. Reported
    both — the number wins; do not paper over. Fix candidate: apply link_margin to the
    completion threshold + a two-sided blended-cost guard, then re-verify.

------------------------------------------------------------------------------------------------
3. PER-CASE PnL percentiles ($/window)
------------------------------------------------------------------------------------------------
case       n      p05      p25      p50      p75      p95      min      max
A        162    +0.66    +1.22    +1.62    +2.23    +3.88    +0.21    +4.40
B        268    +0.79    +1.45    +2.27    +3.63    +4.65    +0.05    +8.12
C       1621    -2.28    -0.70    +0.27    +1.11    +2.21    -3.57    +4.89
D         24    +1.98    +2.74    +3.74    +4.28    +4.60    +1.88    +4.83
E        429    -2.47    -1.18    -0.03    +0.76    +2.15    -3.39    +4.22

------------------------------------------------------------------------------------------------
4. 10 WORST windows by full PnL
------------------------------------------------------------------------------------------------
slug                           day         regime    case  naked@frz    exit_branch        $
btc-updown-5m-1783266000       2026-07-05  reversal  C     5.86         sold           -3.57
btc-updown-5m-1783244400       2026-07-05  reversal  C     5.00         sold           -3.43
btc-updown-5m-1782942000       2026-07-01  reversal  E     5.00         rode_lost      -3.39
btc-updown-5m-1782951300       2026-07-02  reversal  C     5.00         sold           -3.24
btc-updown-5m-1783277400       2026-07-05  reversal  C     5.00         sold           -3.24
btc-updown-5m-1783440900       2026-07-07  reversal  C     5.00         sold           -3.23
btc-updown-5m-1783171500       2026-07-04  chop      C     5.00         sold           -3.20
btc-updown-5m-1783719000       2026-07-10  chop      C     5.00         sold           -3.13
btc-updown-5m-1783958700       2026-07-13  reversal  C     5.08         sold           -3.12
btc-updown-5m-1783146000       2026-07-04  chop      C     5.00         sold           -3.11

------------------------------------------------------------------------------------------------
5. pair_eff sigma for n-sizing (chop-hindsight subset)
------------------------------------------------------------------------------------------------
chop(hindsight) per-window pair_eff: n=1277  mean=0.9150  sigma(pop)=0.0952
APPROXIMATION: pairs within a window are correlated, so we approximate the per-PAIR sigma by
  the per-WINDOW pair_eff sigma (conservative). SE(mean) = sigma / sqrt(n).
    n= 50 pairs -> SE of mean pair_eff = 0.0135
    n= 80 pairs -> SE of mean pair_eff = 0.0106
    n=100 pairs -> SE of mean pair_eff = 0.0095

------------------------------------------------------------------------------------------------
6. RECONCILIATION (must balance to the cent)
------------------------------------------------------------------------------------------------
   case counts: F=28  A=162  B=268  C=1621  D=24  E=429
   Sigma(counts across F,A,B,C,D,E) = 2532   total windows = 2532   diff = 0
   Sigma($ across all cases) = +1249.22   aggregate PnL (all windows) = +1249.22   diff = +0.0000
   OK: reconciliation balances (counts exact, $ within $0.01).

------------------------------------------------------------------------------------------------
7. MEASURED case frequencies vs spec §4 (A~51% clean-ish, D/E trend tail ~20%)
------------------------------------------------------------------------------------------------
   measured (share of TRADED): A=6.5%  B=10.7%  C=64.7%  D=1.0%  E=17.1%
   spec: A~51%% clean-ish ; D+E trend tail ~20%%
   measured A=6.5% (spec ~51%) ; measured D+E=18.1% (spec ~20%)
   -> the DOC (spec), not the numbers, is corrected where they differ.

------------------------------------------------------------------------------------------------
8. MEASURED vs ASSUMED / reliability
------------------------------------------------------------------------------------------------
   Any regime x case cell with 0 < n < 30 is marked '!' in the matrix above -> unreliable (n<30).
   unreliable cells: chop/D(n=19), reversal/A(n=27), reversal/D(n=4), trend/A(n=18), trend/B(n=21), trend/D(n=1)
   MEASURED numbers above supersede any prior report/spec where they disagree (number wins).
================================================================================================
