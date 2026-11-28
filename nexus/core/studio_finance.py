"""Finite, bounded quote/fundamental results from the selected yfinance adapter."""

import math
from datetime import UTC, datetime
from urllib.parse import quote

PRICE_FIELDS = {
    "day_high": ("dayHigh", "regularMarketDayHigh"),
    "day_low": ("dayLow", "regularMarketDayLow"),
    "volume": ("volume", "regularMarketVolume"),
    "fifty_two_week_high": ("fiftyTwoWeekHigh",),
    "fifty_two_week_low": ("fiftyTwoWeekLow",),
}
FUNDAMENTAL_FIELDS = {
    "market_cap": "marketCap",
    "pe_ratio": "trailingPE",
    "forward_pe": "forwardPE",
    "eps": "trailingEps",
    "dividend_yield": "dividendYield",
    "ex_dividend_date": "exDividendDate",
    "revenue": "totalRevenue",
    "profit_margin": "profitMargins",
    "operating_margin": "operatingMargins",
    "debt_to_equity": "debtToEquity",
    "return_on_equity": "returnOnEquity",
    "book_value": "bookValue",
}


def stock_result(ticker, operation, info):
    """Preserve zero/missing values and vendor units; never expose raw SDK records."""
    if not isinstance(info, dict) or not info:
        raise ValueError("No stock information is available for this ticker")

    def number(*keys):
        value = next((info[key] for key in keys if info.get(key) is not None), None)
        if value is not None and (
            type(value) not in {int, float}
            or (type(value) is int and value.bit_length() > 1023)
            or not math.isfinite(value)
        ):
            raise ValueError("Finance provider returned an invalid numeric field")
        return value

    def text(*keys, maximum=300):
        value = next((info[key] for key in keys if info.get(key) not in (None, "")), None)
        if value is not None and (
            not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Finance provider returned an invalid text field")
        return value

    if operation == "price":
        current = number("regularMarketPrice", "currentPrice")
        previous = number("previousClose", "regularMarketPreviousClose")
        if current is None:
            raise ValueError("No current stock quote is available for this ticker")
        change = round(current - previous, 2) if previous is not None else None
        percent = round(change / previous * 100, 2) if previous not in {None, 0} else None
        if any(value is not None and not math.isfinite(value) for value in (change, percent)):
            raise ValueError("Finance quote change exceeds finite numeric bounds")
        result = {
            "current_price": current,
            "previous_close": previous,
            "change": change,
            "change_percent": percent,
            **{name: number(*keys) for name, keys in PRICE_FIELDS.items()},
            "market_time": number("regularMarketTime"),
            "currency": text("currency", maximum=16),
        }
    elif operation == "fundamentals":
        company = text("shortName", "longName")
        if not company:
            raise ValueError("No company information is available for this ticker")
        result = {
            "company_name": company,
            "sector": text("sector"),
            "industry": text("industry"),
            **{name: number(key) for name, key in FUNDAMENTAL_FIELDS.items()},
            "currency": text("financialCurrency", "currency", maximum=16),
        }
    else:
        raise ValueError("Choose finance operation price or fundamentals")
    return {
        "status": "success",
        "ticker": ticker.upper(),
        "operation": operation,
        **result,
        "missing_fields": [name for name, value in result.items() if value is None],
        "source": "Yahoo Finance via yfinance",
        "source_url": "https://finance.yahoo.com/quote/" + quote(ticker.upper(), safe="") + "/",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "notice": "Latest provider snapshot, potentially delayed; not streaming. Financial metrics retain provider units. Missing fields are null.",
    }
