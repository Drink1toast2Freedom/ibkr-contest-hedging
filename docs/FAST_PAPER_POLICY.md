# Faster target-aware paper policy

The operator selected these practice settings on October 7, 2026:

| Setting | Previous | Active practice setting |
|---|---:|---:|
| Stock/ETF evaluation and ordinary plan cooldown | 60 seconds | 15 seconds |
| Full option/stock/ETF solve and option-plan cooldown | 15 minutes | 2 minutes |
| Minimum modeled score-improvement floor | $100 | $25 |
| Risk monitoring | 5 seconds | 5 seconds |
| Estimation uncertainty multiplier | 1.645 | 1.645 |

These are evaluation intervals, not a promised order count or exact real-time
latency. An active execution plan must complete or reconcile before another plan
starts. The full-portfolio timer is independently checked so it cannot be
starved by repeated stock-only solves. Solver deadlines, ordinary closing
cutoffs, quote freshness, mandate protection and all risk limits remain enforced.

The optimizer minimizes expected absolute closing NAV deviation from the
interest-adjusted previous official close. Its existing objective already
contains today's full NAV gap; it does not merely minimize changes from the
current intraday NAV. Tests verify that a $4,000 deficit can select a profitable
target-closing trade under controlled positive-return scenario assumptions,
where the same trade is not selected when already at target. Those assumptions
are a test fixture, not evidence that such returns will occur in the market.

The dashboard now shows the gap used by each solve, expected distance if holding
versus trading, and the specific no-trade reason. Decision records also identify
stock-only versus full optimization. The trade threshold is the larger of $25
and the uncertainty allowance; costs are already inside the compared scores.

The current return model has zero directional drift. It measures joint historical
covariance and models price/IV uncertainty and nonlinear option behavior. It
cannot infer a profitable direction merely because the account is below target.
A predictive recovery strategy would need an independently validated forecast;
no recovery coefficient or profit assumption is fabricated here. A hedge can
reduce future risk without undoing a loss already reflected in NAV. More
frequent evaluation and smaller thresholds do not guarantee recovery and may
still produce no qualifying trade.

This operator-selected paper profile is not established as contest-optimal.
Track realized costs, actual score and adverse execution before making stronger
performance claims.
