"""Reconstruct the guru's full 1:45-2:00 ET window from his trade tape.
Up won (price ran 0.74 -> 0.96). Redeem was 557.5 shares = $557.54.
Goal: total Up qty, total Down qty, $ spent each, pairs, locked edge, PnL.
"""
# (price_cents, shares) from the on-screen tape; UP and DOWN buys, full window
UP = [(96,30),(96,30),(95,30),(94,19),(95,19),
      (94,6),(94,15),(94,26),(93,30),(88,30),
      (87,15),(81,10),(82,30),(87,30),
      (85,20),(85,20),(88,10.4),(88,30),(87,13.2),(88,19.6),(88,10),(88,8.3),(88,1.7),(88,10),(88,30),
      (87,13.6),(89,30),(74,20.7)]
DOWN = [(1,30),(2,30),(2,23.9),(3,4.7),(4,30),(3,25.3),(4,30),(5,30),
        (3,11),(3,5.2),(3,7),(3,6.9),(5,1.9),(5,5),(5,5),(5,1.3),(5,5),
        (10,30),(9,30),(10,30),(12,0.2),(12,11.4),(12,11.4),(12,11.4),(12,7.3),(12,11.4),(12,8.4),
        (13,5),(13,20.3),(13,4.7),(10,30),(11,30)]

up_q = sum(s for _, s in UP);     up_d = sum(p/100*s for p, s in UP)
dn_q = sum(s for _, s in DOWN);   dn_d = sum(p/100*s for p, s in DOWN)
spent = up_d + dn_d
pairs = min(up_q, dn_q)
# pair cost = proportional Up$ for the paired Up + all Down$ (Down is fully paired here)
pair_up_d = up_d * (pairs / up_q)
pair_cost = (pair_up_d + dn_d) / pairs
excess_up = up_q - pairs
excess_up_d = up_d * (excess_up / up_q)

print("UP   : %.1f shares, $%.2f  (avg %.3f)" % (up_q, up_d, up_d/up_q))
print("DOWN : %.1f shares, $%.2f  (avg %.3f)" % (dn_q, dn_d, dn_d/dn_q))
print("spent total           : $%.2f" % spent)
print("balanced PAIRS         : %.1f  @ avg $%.3f/pair  (< $1 => locked +$%.2f)"
      % (pairs, pair_cost, pairs*(1-pair_cost)))
print("excess UP (favorite tilt): %.1f shares, cost $%.2f" % (excess_up, excess_up_d))
print()
print("IF UP wins  : %.1f x $1 = $%.2f  -> PnL %+.2f" % (up_q, up_q, up_q - spent))
print("IF DOWN wins: %.1f x $1 = $%.2f  -> PnL %+.2f" % (dn_q, dn_q, dn_q - spent))
print()
print("actual redeem on screen: $557.54 (Up won)")
print("=> %.1f%% return on $%.0f invested in ONE 15m window" % (100*(up_q-spent)/spent, spent))
