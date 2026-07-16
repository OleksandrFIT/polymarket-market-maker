# Endgame probability — P(favourite wins | price, T-t)

OFFLINE, read-only over recorded tapes. 2543 windows.
Favourite = side priced > 0.5 at that instant (causal: last mid at or before T-t).
A late MAKER bid on the favourite is +EV only if P(win) is materially ABOVE the price paid.
If P(win) tracks the price, the market is efficient there and the branch is DEAD.

## T-20s  (n=2542 observations)
price bucket        n    P(win) mid-price 95% Wilson CI          verdict
[0.85,0.88)        76    0.9211     0.865 [0.8383, 0.9633]  tracks price (efficient)
[0.88,0.90)        68    0.7794     0.890 [0.6674, 0.8615]  BELOW price -> -EV
[0.90,0.92)        74    0.8784     0.910 [0.7847, 0.9347]  tracks price (efficient)
[0.92,0.95)       152    0.9671     0.935 [0.9253, 0.9859]  tracks price (efficient)
[0.95,0.98)       260    0.9654     0.965 [0.9355, 0.9817]  tracks price (efficient)

  HEADLINE  P(win | price in [0.88,0.95)) at T-20s = 0.9014  (n=294, 95% CI [0.8619, 0.9304])
            a maker bid inside this band pays ~0.88-0.95; P(win) overlaps the band -> market efficient here -> branch DEAD

## T-15s  (n=2539 observations)
price bucket        n    P(win) mid-price 95% Wilson CI          verdict
[0.85,0.88)        63    0.9365     0.865 [0.8478, 0.9750]  tracks price (efficient)
[0.88,0.90)        54    0.9630     0.890 [0.8746, 0.9898]  tracks price (efficient)
[0.90,0.92)        69    0.9130     0.910 [0.8230, 0.9595]  tracks price (efficient)
[0.92,0.95)       139    0.9496     0.935 [0.8997, 0.9754]  tracks price (efficient)
[0.95,0.98)       252    0.9841     0.965 [0.9599, 0.9938]  tracks price (efficient)

  HEADLINE  P(win | price in [0.88,0.95)) at T-15s = 0.9427  (n=262, 95% CI [0.9077, 0.9650])
            a maker bid inside this band pays ~0.88-0.95; P(win) overlaps the band -> market efficient here -> branch DEAD

## T-10s  (n=2542 observations)
price bucket        n    P(win) mid-price 95% Wilson CI          verdict
[0.85,0.88)        45    0.8667     0.865 [0.7382, 0.9374]  tracks price (efficient)
[0.88,0.90)        44    0.9091     0.890 [0.7884, 0.9641]  tracks price (efficient)
[0.90,0.92)        62    0.9194     0.910 [0.8247, 0.9651]  tracks price (efficient)
[0.92,0.95)       131    0.9618     0.935 [0.9138, 0.9836]  tracks price (efficient)
[0.95,0.98)       219    0.9680     0.965 [0.9355, 0.9844]  tracks price (efficient)

  HEADLINE  P(win | price in [0.88,0.95)) at T-10s = 0.9409  (n=237, 95% CI [0.9033, 0.9645])
            a maker bid inside this band pays ~0.88-0.95; P(win) overlaps the band -> market efficient here -> branch DEAD

NOTE: this is the resolution probability only. It ignores fill probability (a late maker bid
may simply not fill) and adverse selection (it fills exactly when it is about to be wrong).
So it is an UPPER bound on the branch's value: if the number is not clearly above the price,
the branch cannot be rescued by execution.
