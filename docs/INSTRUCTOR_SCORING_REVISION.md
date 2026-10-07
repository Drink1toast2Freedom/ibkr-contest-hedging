> **Latest clarification:** The organizer uses the actual daily cash rate, not fixed 3.35%. Current practice mode is `actual_daily_cash_rate`; target = previous official ending NAV × (1 + sourced daily fractional rate). Missing rates leave targets/scores unknown. Accrued interest is not subtracted separately. Earlier fixed-rate examples below are superseded. Record dated rates under Settings & alerts.

# Confirmed formula — October 6, 2026

The instructor supplied **sum |V_i − V_(i−1) × (1 + r/360)|**, with r approximately **3.35%**. Use the full previous closing NAV and one rate/360 factor for each configured scored-session difference, exactly as shown. Do not subtract actual accrued cash interest separately or substitute cash balance for prior NAV. Both signs count equally. Raw closing inputs remain revisioned; corrections recompute adjacent scores. Current API NAV is provisional and official IBKR Custom Statement Net Asset Value remains the closing input.

The benchmark rate is distinct from the account-specific financing forecast. Actual NAV can differ from the ideal all-cash benchmark due to rates, eligibility thresholds, accrual timing, and report rounding. No deposit interest is counted twice.

## Earlier clarification (superseded uncertainty)

# Instructor clarification: scoring and data

The user supplied the instructor's response:

- Other students reportedly could not retrieve live API quotes and used historical/recent data, possibly the approximately 15-minute-delayed feed. This is evidence about availability, not a confirmed subscription policy or permission to relabel delayed quotes as live.
- The instructor will revise the scoring calculation to discount daily NAV changes for cash interest. An all-cash portfolio should score zero.
- The instructor uses Net Asset Value from an IBKR Custom Statement. In the account web portal, use Statements → + beside Custom Statements → include Net Asset Value.

The exact interest adjustment is not yet provided. Subtracting the account's actual daily earned interest, comparing against a growing all-cash benchmark, and discounting NAV by a cumulative interest factor are different rules. They can all give all-cash zero under their assumptions but can rank hedged portfolios differently.

Required clarification: the final daily/total formula; discount rate or cash benchmark; calendar-day/weekend treatment; treatment of positive interest versus margin/borrow charges; and the statement date/value used for each scoring session.

Maintain raw IBKR NAV, cash, interest accrual and report provenance. The gross NAV still includes interest. Change the score transformation and optimizer objective only after the organizer's formula is confirmed. Do not set the interest forecast to zero as a substitute for discounting it.

The practice manifest is unconfirmed with scoring_rule=pending_interest_adjustment. Trading is paused, opening purchases disarmed, and the official score is unknown rather than a fabricated zero. Historical/recent data can be investigated independently for labeled shadow/replay testing while the scoring formula is pending. Automatic execution retains its live-data requirements.
