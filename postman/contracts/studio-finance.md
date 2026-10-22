# Studio finance tool contract

Reuse the existing registered `studio:<workspace>:finance` tool through agents,
workflow tool nodes, or `POST /api/runs` with `kind: tool`. No new public route,
credential store or provider selection is introduced.

Arguments: `ticker` plus optional `operation` (`history`, `price`, `fundamentals`).
Omitting operation preserves the existing history behavior and its `period`
argument (`1mo`, `3mo`, `6mo`, `1y`). Price/fundamentals do not accept period.
Every operation requires explicit private `research.finance: true` configuration.
Readiness includes yfinance installation. No lookup executes while browsing the
catalog. No trading or write operation exists.

Price returns current/previous close, change/percent, high/low, volume, 52-week
range, currency and provider market timestamp. Fundamentals returns company,
sector, industry and the frozen plugin's valuation, earnings, dividend, revenue,
margin, debt/equity, return/equity and book-value fields. Results retain null for
missing values and zero for known zero. Absent primary quote/company identity
fails; malformed numeric/text fields fail. Responses include retrieval time,
source URL, explicit missing fields and delayed-data/provider-unit notice.

The default SDK path runs in the existing isolated Python subprocess with a
40-second bound, sanitized environment and 256 KiB output bound. The worker
emits only selected SDK fields for price/fundamentals. Failure never falls back
to another provider, performs a trade, or returns a partial success result.

Frozen source: community commit `f3a733c1470fc79d7dbc7fc21ddeab077f4645f4`,
`finance/src/finance/tools.py`, Git blob `dded2ab0b5dc84eaea9ebbb21a59909a2070749e`.
Native operations replace the two SAM Python function names. Unlike source
truthiness fallbacks, zero values are retained. Long-name-only company records
are accepted. Provider failures raise sanitized errors rather than echoing raw
SDK exceptions. No Solace broker, Google ADK or source package is executed.
