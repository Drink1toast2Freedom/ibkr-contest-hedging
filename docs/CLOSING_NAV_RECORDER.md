# Automatic IBKR closing NAV recording

The app requests IBKR `NetLiquidation` at the exchange close for Day 0 and every
configured scoring session. It uses the exchange calendar, including early
closes, and runs independently of paper/shadow/paused mode, quote quality and
optimization. No dates are hardcoded in this workflow.

Defaults are enabled, a 180-second capture window and a 15-second retry interval.
These are validated through `closing_capture` in the contest manifest:

```yaml
closing_capture:
  enabled: true
  window_seconds: 180
  retry_seconds: 15
```

The recorder requests a fresh account summary and requires a new NAV event for
the configured paper account. It does not reuse a cached summary, a pre-close
value, another account or moomoo data. Raw NAV includes accrued interest and
broker charges; nothing is subtracted or added again.

Observations enter the revisioned NAV ledger with scheduled-close and actual
observation timestamps and API provenance. The next session uses the recorded
previous close and sourced cash benchmark or separately labeled approved forecast.
API captures are marked as awaiting statement reconciliation. Importing the
organizer's corrected IBKR statement revises the value and adjacent scores.

An existing close is retained, including when a report arrives during the API
request. Restarts do not duplicate captures or overwrite a statement. Failed
requests retry only within the capture window. A missed window creates a durable
alert requesting a statement import; starting later does not substitute today's
NAV for a missed close.

Keep the Mac awake, service running and Paper TWS authenticated at the close.
The launcher prevents idle sleep; laptop closure or TWS shutdown can still
prevent capture. Settings shows recorder status and next scheduled close.
Disconnected broker or failed storage cannot create a fabricated closing value.

The operator supplied October 7's statement NAV of $995,294.19. Its import gives
an estimated October 8 target of $995,386.81 under the approved approximate rate.
Official scoring still requires the actual daily rate. Limits remain unchanged.

Broker initialization and reconciliation share a lock. Contract qualification
has a 15-second deadline; failure disconnects and clears partial readiness for
retry. Closing-summary subscriptions and handlers are removed after requests.
