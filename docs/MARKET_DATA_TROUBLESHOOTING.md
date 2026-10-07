# IBKR paper market-data troubleshooting

There are two separate checks: a functioning local TWS socket connection, and permission to deliver market data through that API. Reading account NAV successfully does not establish stock/option quote permissions. Quotes visible inside TWS may use different data licences from API quotes.

## 1. Restore the local paper connection

Start TWS, select Paper Trading and complete login. Keep it open. Under Global Configuration → API → Settings, verify ActiveX and Socket Clients are enabled, socket port is 7497, and localhost access is permitted. Do not change to a live account. Click Apply/OK. Click Reconcile on the local dashboard and check that it shows connected. A connection-refused error must be resolved before quote permissions can be tested.

## 2. Identify the paper-account type

- **Instructor-created Student Trading Lab:** API functionality is supported, but public documentation does not establish that every student can self-enable live API market-data entitlements. If the acknowledgement/subscription menu is absent, involve the instructor and IBKR Student Trading Lab support.
- **Standalone Free Trial:** IBKR documents delayed data (10–15 minutes for most products), rather than unrestricted real-time data. A missing live-account subscription menu is therefore not evidence of a broken local API installation.
- **Paper account linked to a live account:** configure API acknowledgement and market-data subscriptions under the authorized live-account login, then share eligible subscriptions with the paper user. Execution remains paper-only.

Do not open/fund a live account or buy data just to troubleshoot before identifying which case applies.

## 3. For a linked live account

In the live account's Client Portal, open Settings → Market Data Subscriptions. Find Market Data API Acknowledgement, enable it if offered, review/sign the required form yourself, and log out/in as IBKR instructs. Subscription settings and menus can vary with account type and permissions.

Confirm API-capable streaming coverage for BA stock (NYSE Network A/CTA) and US equity options (OPRA), or an appropriate bundle. The underlying subscription is needed alongside options data for Greeks. Check existing entitlements first; fees or new agreements require the account owner's decision.

Under Settings → Account Configuration → Paper Trading Account, enable real-time data sharing with the intended paper username. Then reconnect paper TWS. Seeing free quotes inside TWS does not prove that an API request is entitled to the same feed.

## 4. For Student Trading Lab without those menus

Ask the instructor or IBKR's Student Trading Lab representative to inspect the student's username-level API data entitlements. Suggested inquiry:

> My Student Trading Lab paper account can retrieve account NAV and positions through the TWS API, but live BA stock and October-expiry BA option requests return errors 354/10090/10168. The student portal does not show Market Data API Acknowledgement. Does this account support real-time API stock and OPRA data? If so, please explain how the acknowledgement and entitlements can be enabled for my paper username. If only delayed API data is available, please confirm that limitation.

Provide the actual error messages and account ID privately to IBKR/instructor as appropriate. Do not send passwords or authentication tokens. This inquiry does not assume the instructor has a subscription switch in their portal or that a live subscription can be shared with an unrelated student account.

## 5. Verify the result

After IBKR confirms access, log back into paper TWS and click Reconcile to retry subscriptions. Test during regular trading hours, since closed markets can legitimately lack current bids/asks. The system needs live bid/ask prices and sizes for BA and the required/selected options, plus fresh option IV/Greeks. Its automatic trading gate rejects delayed and frozen data.

A diagnostic request for market-data type 3 (delayed), or 4 (delayed-frozen outside market hours), can help distinguish a working data connection from missing live entitlements. It cannot upgrade permission to real-time. The current execution service requests type 1 (live); it does not automatically switch execution to delayed quotes. Do not weaken freshness checks to pass a delayed feed.

If IBKR confirms real-time API data is unavailable for this account, use delayed/synthetic data for explicitly labeled shadow/mechanics testing, or evaluate a separately licensed live-data provider and its adapter. Automatic competition hedging cannot be considered ready until the chosen live feed is validated.

## Official references

- API versus TWS data licensing: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/tws-data-vs-api-data
- Paper portal limitations and sharing: https://www.ibkrguides.com/clientportal/papertradingaccount.htm
- API acknowledgement procedure: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/compliance-requirements-for-api-market-data/market-data-api-acknowledgement
- Stock/option coverage and underlying requirements: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/popular-market-data-subscriptions/introduction
- Paper/live sharing: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-users/market-data-sharing
- Student Trading Lab/API overview and representative contact form: https://www.interactivebrokers.com/campus/student-trading-lab/tws-python-bridge-theory-and-practice/
- Free Trial data limitation: https://api.ibkr.com/en/trading/free-trial.php
- Delayed/frozen data types: https://www.interactivebrokers.com/docs/tws-api/doc/market-data-delayed/introduction
