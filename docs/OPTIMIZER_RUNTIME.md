# Optimizer runtime improvements

The objective remains absolute, interest-adjusted closing NAV deviation. Mandatory
quantities, integer positions, transaction-cost relationships, liquidity, margin,
cardinality, borrow, delta, gross-notional and stress limits remain enforced.
No surplus-expense controller or new directional forecast is activated.

## Model changes

- Only net target quantities need integer domains. Sign and trade-direction gates
  imply integer positive/negative holdings and buy/sell quantities. Zero-current
  positions reuse trade-direction indicators for sign and selection. Forced signs
  use constants instead of additional binaries.
- Stock bounds incorporate available borrow and an implied delta interval when
  its symbol has only one variable stock and frozen options.
- Equal buy/sell execution schedules share one exact slice count. One-contract
  option slices use the integer trade quantity directly. Asymmetric displayed
  liquidity retains separate direction-specific schedules.
- Count remainder allowances are gated by trade activity. Inactive trades cannot
  contribute fractional phantom commissions to the LP relaxation; integer cost
  relationships remain exactly the same as before.
- Cash positive-part functions use proved interval bounds and omit binaries when
  the sign is fixed. Zero cash-credit rates omit the unused eligible-credit split.
  Original absolute cash bounds remain in force.
- Stress rows with identical candidate coefficients retain the lowest fixed
  portfolio value, which is the strongest constraint in that group. Exact floating
  equality is required; no stress sampling, rounding or approximation is used.
- A single parameterized CVXPY problem serves primary, next-close and turnover
  stages. Public warm starts provide HiGHS with the previous solution while the
  canonical variable layout remains unchanged.

## Certified score selection and bounded ranking

The original design permits selecting a portfolio within $1 of the optimal modeled
primary score. Primary solves now request a $0.25 absolute gap, with zero relative
gap. If L is HiGHS' certified lower bound and P* is the true model optimum, the
subsequent constraint is primary score <= L + $1. Since L <= P*, every returned
portfolio stays within $1 of P*. The limit is not incumbent + $1, which could
otherwise add the incumbent's gap to the tolerance. The baseline profiles keep
their existing 0.01 selection band and use a 0.0025 initial gap.

No unsuccessful primary solve is accepted. Status, finite bounds and the actual
primal-dual gap are checked. Numerical costs and cash carry are recalculated after
integer rounding, and the returned objective certificate is checked again.
Independent execution risk/economics gates still decide whether an order can trade.

Next-close selection uses the same lower-bound method for its $1 band. Turnover
keeps its 1e-6 gap. Secondary work shares a configurable one-second budget and
32-node per-stage limit (`policy.ranking_seconds`, `policy.ranking_nodes`). The
next-close attempt receives at most half the ranking time. If it cannot finish,
turnover is not run out of order. Only a copied solution from a successfully
certified earlier stage is retained; later timeout values are discarded.

`Target.status == OPTIMAL` means an accepted certified target in the declared
selection band. It does not assert that optional ranking finished or that the
initial primary gap was exactly zero. Decisions record the accepted stage,
ranking completeness, objective lower bound, actual returned objective, variable
counts, reduced stress rows, node counts, and phase/total timings. The dashboard
shows runtime and incomplete-ranking status. Failed primary certificates produce
no-trade targets with an explicit failure status.

The overall ten-second budget now includes formulation/canonicalization. Calls
are single-threaded with the existing fixed seed. Node limits reduce ranking
variability, but wall-time expiry under different machine loads can still affect
whether a solve finishes; it never permits an uncertified target.

## Offline evidence and reproduction

Use the diagnostic below from the repository. It uses synthetic quotes and
mandatory-only inventory, never connects to IBKR, Moomoo or PostgreSQL, and does
not submit orders. Configuration is used to size the universe, not as live data.

```sh
.venv/bin/python ops/benchmark_optimizer.py --baseline --baseline-ref 6b9d1fc --repeats 3
.venv/bin/python ops/benchmark_optimizer.py --config config/local.practice.yaml --baseline --baseline-ref 6b9d1fc --repeats 1
```

Initial measurements on the configured 24-instrument/14-option universe changed
full-portfolio computation from a roughly ten-second primary timeout to roughly
two seconds with a certified primary target. A harder stock-only case still took
roughly nine seconds; it produced a certified target rather than discarding a
successful primary solve due to unfinished secondary work. Final repeated timings
are recorded in the validation report. These are computation measurements, not
historical contest-score improvement, market execution latency, or a guarantee
that every future solve will meet its deadline.

Validation covers exact cash-sign functions, asymmetric slice counts, brute-force
integer objective comparisons, borrow/holdings constraints, exact stress-row
dominance, rounded objective certificates, rejected invalid bounds, primary
deadlines, retention after failed ranking, and honest ranking-status reporting.
The full existing scoring, valuation, broker, execution, dashboard and recovery
regression suite remains required before deployment.
