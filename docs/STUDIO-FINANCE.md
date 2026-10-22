# Finance history, quotes and company fundamentals

The built-in finance tool now supports the frozen community plugin's two defining
operations in addition to existing history. It remains a read-only, opt-in adapter
using the selected yfinance SDK, with no new provider account or trading path.

Enable `research.finance: true` in the private runtime configuration and install
the `studio-research` extra. The catalog records yfinance `0.2.65` as its pinned
acceptance version. Restart Studio after administrator configuration changes.
Browsing the catalog never installs software or queries a market service.

Choose the registered `studio:<workspace>:finance` tool in an agent or workflow:

```json
{"ticker": "EXAMPLE", "operation": "price"}
```

```json
{"ticker": "EXAMPLE", "operation": "fundamentals"}
```

Existing calls stay readable and keep their behavior:

```json
{"ticker": "EXAMPLE", "period": "3mo"}
```

Omitting operation selects history. Only history accepts period. Quotes provide
current/previous close, changes, daily high/low, volume, 52-week range, currency
and provider market time. Fundamentals provide company/sector/industry plus the
frozen plugin's valuation, earnings, dividend, revenue, margins and balance-sheet
metrics. Missing optional fields are null; zero values stay zero. Missing primary
quote/company identity or malformed values fail. Financial units remain exactly
as supplied by the provider, with an explicit notice rather than inferred unit
conversions. Retrieval time and a fixed-provider source URL accompany each result.
A latest snapshot may be delayed; no real-time streaming is claimed.

Native operation names replace SAM's `get_stock_price` and
`get_stock_fundamentals`. Compared with the frozen source's truthiness fallbacks,
Nexus preserves zeros and accepts a valid long-name-only company record. Errors
are sanitized instead of echoing raw SDK exceptions. The adapter does not import
or execute Solace/Google ADK code. Existing history injection retains its two-
argument callable contract; embedders may inject `finance_info_client(ticker)`
for raw company information. The default production path uses yfinance.

[Source contract](https://github.com/solacecommunity/solace-agent-mesh-plugins/blob/f3a733c1470fc79d7dbc7fc21ddeab077f4645f4/finance/src/finance/tools.py)
and [SDK method](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_info.html).

## Acceptance and boundaries

- Pure contracts cover fields, zero/null distinction, fallbacks, finite numbers,
  malformed values, opt-in, old history calls and sanitized subprocess failures.
- The actual pinned yfinance SDK and worker run against local Postman HTTP
  fixtures for quote, fundamentals, zero, missing identity, throttling and invalid
  JSON. Only SDK destination routing and cookie bootstrap are replaced; the SDK
  parser, HTTP client, worker field filter and native normalization run normally.
- The framework example runs a two-node workflow through an actual disposable
  Studio HTTP server and verifies read-only run inspection:
  `PYTHONPATH=. python examples/nexus-bot/src/studio_finance.py`.
- Real Yahoo availability, cookie/crumb authentication, rate limits, data quality,
  provider terms and deployed-VM/device checks remain live validation pending.
  Fixture success does not establish live prices or investment suitability.

The worker has a 40-second runtime bound, a sanitized environment and a 256 KiB
output bound. It emits selected SDK fields only. The adapter never switches to
another service on failure. The SDK may perform its own read-only request retry
for cookie strategy; there is no mutation to replay. No credential, state or
configuration migration is required. Existing exports remain valid.

Contract: [Studio finance](../postman/contracts/studio-finance.md).
Simulation: `postman/simulations/studio-finance.sim.yaml`.
