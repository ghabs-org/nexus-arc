"""Trusted bounded yfinance subprocess. No user code or credentials accepted."""

import json
import sys


def main():
    import yfinance

    ticker, period = sys.argv[1:3]
    operation = sys.argv[3] if len(sys.argv) > 3 else "history"
    selected = yfinance.Ticker(ticker)
    if operation in {"price", "fundamentals"}:
        info = selected.get_info()
        if not isinstance(info, dict):
            raise ValueError("No company information is available")
        fields = (
            (
                "regularMarketPrice",
                "currentPrice",
                "previousClose",
                "regularMarketPreviousClose",
                "dayHigh",
                "regularMarketDayHigh",
                "dayLow",
                "regularMarketDayLow",
                "volume",
                "regularMarketVolume",
                "fiftyTwoWeekHigh",
                "fiftyTwoWeekLow",
                "regularMarketTime",
                "currency",
            )
            if operation == "price"
            else (
                "currency",
                "financialCurrency",
                "shortName",
                "longName",
                "sector",
                "industry",
                "marketCap",
                "trailingPE",
                "forwardPE",
                "trailingEps",
                "dividendYield",
                "exDividendDate",
                "totalRevenue",
                "profitMargins",
                "operatingMargins",
                "debtToEquity",
                "returnOnEquity",
                "bookValue",
            )
        )
        print(
            json.dumps({"info": {key: info[key] for key in fields if key in info}}, allow_nan=False)
        )
        return
    if operation != "history":
        raise ValueError("Unknown finance operation")
    history = selected.history(period=period, interval="1d", timeout=10, raise_errors=True)
    if history.empty or len(history) > 400:
        raise ValueError("No bounded price history is available")
    rows = json.loads(history.reset_index().to_json(orient="records", date_format="iso"))
    print(
        json.dumps(
            {
                "ticker": ticker,
                "period": period,
                "source": "Yahoo Finance via yfinance",
                "rows": rows,
            }
        )
    )


if __name__ == "__main__":
    main()
