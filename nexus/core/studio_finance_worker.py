"""Trusted bounded yfinance subprocess. No user code or credentials accepted."""

import json
import sys


def main():
    import yfinance

    ticker, period = sys.argv[1:3]
    history = yfinance.Ticker(ticker).history(
        period=period, interval="1d", timeout=10, raise_errors=True
    )
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
