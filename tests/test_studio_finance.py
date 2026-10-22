"""Frozen finance operations retain finite data, units, missing values and opt-in."""

import asyncio
import json
import runpy
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_finance import stock_result

INFO = {
    "regularMarketPrice": 125.5,
    "previousClose": 120.0,
    "dayHigh": 127.0,
    "dayLow": 119.0,
    "volume": 1000,
    "fiftyTwoWeekHigh": 150.0,
    "fiftyTwoWeekLow": 75.0,
    "currency": "USD",
    "financialCurrency": "USD",
    "regularMarketTime": 1700000000,
    "shortName": "Example Company",
    "sector": "Technology",
    "industry": "Software",
    "marketCap": 1000000,
    "trailingPE": 20.0,
    "forwardPE": 18.0,
    "trailingEps": 6.0,
    "dividendYield": 0,
    "totalRevenue": 10000,
    "profitMargins": 0.25,
    "operatingMargins": 0.3,
    "debtToEquity": 12.0,
    "returnOnEquity": 0.2,
    "bookValue": 24.0,
    "exDividendDate": 1700000000,
}


def test_quote_and_fundamentals_match_frozen_source_fields_without_truthiness_loss():
    original = deepcopy(INFO)
    quote = stock_result("example", "price", INFO)
    assert quote["ticker"] == "EXAMPLE" and quote["current_price"] == 125.5
    assert quote["change"] == 5.5 and quote["change_percent"] == 4.58
    assert quote["volume"] == 1000 and quote["fifty_two_week_low"] == 75
    fundamentals = stock_result("example", "fundamentals", INFO)
    assert fundamentals["company_name"] == "Example Company"
    assert fundamentals["pe_ratio"] == 20 and fundamentals["dividend_yield"] == 0
    assert (
        fundamentals["profit_margin"] == 0.25
    )  # Provider fraction, no implicit percent conversion.
    assert fundamentals["currency"] == "USD" and not fundamentals["missing_fields"]
    assert INFO == original
    assert quote["source_url"].endswith("/EXAMPLE/") and "delayed" in quote["notice"]
    assert quote["retrieved_at"].endswith("+00:00")


def test_zeros_fallbacks_and_missing_fields_are_distinct():
    quote = stock_result(
        "ZERO",
        "price",
        {
            "regularMarketPrice": 0,
            "currentPrice": 50,
            "previousClose": 0,
            "regularMarketPreviousClose": 20,
            "volume": 0,
            "regularMarketVolume": 200,
        },
    )
    assert (
        quote["current_price"] == quote["previous_close"] == quote["change"] == quote["volume"] == 0
    )
    assert quote["change_percent"] is None and "change_percent" in quote["missing_fields"]
    missing = stock_result(
        "MISSING", "fundamentals", {"longName": "Long Name Only", "marketCap": 0}
    )
    assert missing["company_name"] == "Long Name Only" and missing["market_cap"] == 0
    assert missing["revenue"] is None and "revenue" in missing["missing_fields"]
    assert (
        stock_result("FALLBACK", "price", {"currentPrice": 10, "regularMarketPreviousClose": 8})[
            "change_percent"
        ]
        == 25
    )


@pytest.mark.parametrize("info", [{}, [], None, {"volume": 4}])
def test_price_requires_primary_quote(info):
    with pytest.raises(ValueError):
        stock_result("X", "price", info)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), float("-inf"), True, "1.2", [], {}, 10**1000]
)
def test_malformed_numbers_never_become_partial_success(value):
    with pytest.raises(ValueError, match="numeric"):
        stock_result("X", "price", {"currentPrice": value})
    with pytest.raises(ValueError, match="numeric"):
        stock_result("X", "fundamentals", {"shortName": "Example", "marketCap": value})


@pytest.mark.parametrize("value", [42, False, [], {}, "PRIVATE\nDETAIL", "x" * 301])
def test_invalid_company_text_is_rejected_without_echoing_contents(value):
    with pytest.raises(ValueError) as error:
        stock_result("X", "fundamentals", {"shortName": value})
    assert "PRIVATE" not in str(error.value)


def test_extreme_quote_change_cannot_emit_infinity():
    with pytest.raises(ValueError, match="finite"):
        stock_result("X", "price", {"currentPrice": 1e308, "previousClose": -1e308})


def test_opt_in_and_legacy_history_contract_remain_independent(tmp_path):
    calls = []
    studio = StudioService(
        tmp_path,
        research_options={
            "finance_client": lambda ticker, period: {
                "ticker": ticker,
                "period": period,
                "rows": [],
            },
            "finance_info_client": lambda ticker: calls.append(ticker) or deepcopy(INFO),
        },
    )
    try:
        with pytest.raises(ValueError, match="Explicitly enable"):
            studio.research.finance({"ticker": "EXAMPLE", "operation": "price"})
        assert not calls
        studio.research.config["finance"] = True
        history = studio.research.finance({"ticker": "EXAMPLE", "period": "3mo"})
        assert history == {"ticker": "EXAMPLE", "period": "3mo", "rows": []}
        for operation in ("price", "fundamentals"):
            assert (
                asyncio.run(
                    studio.research.execute(
                        "finance", {"ticker": "example", "operation": operation}
                    )
                )["status"]
                == "success"
            )
        assert calls == ["EXAMPLE", "EXAMPLE"]
        for arguments in [
            {"ticker": "../secret", "operation": "price"},
            {"ticker": "EXAMPLE", "operation": "trade"},
            {"ticker": "EXAMPLE", "operation": []},
            {"ticker": "EXAMPLE", "operation": "price", "period": "1mo"},
        ]:
            with pytest.raises(ValueError):
                studio.research.finance(arguments)
        assert len(calls) == 2
    finally:
        studio.close()


@pytest.mark.parametrize("operation", ["price", "fundamentals"])
def test_actual_worker_filters_raw_sdk_fields(operation, monkeypatch, capsys):
    calls = []

    class Ticker:
        def __init__(self, symbol):
            calls.append(symbol)

        def get_info(self):
            return {
                **INFO,
                "PRIVATE_EXTRA": "not needed",
                "longBusinessSummary": "not needed",
                **(
                    {"trailingPE": float("nan")}
                    if operation == "price"
                    else {"regularMarketPrice": float("nan")}
                ),
            }

        def history(self, **kwargs):
            pytest.fail("Price/fundamentals must not accidentally load history")

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=Ticker))
    monkeypatch.setattr(sys, "argv", ["worker", "EXAMPLE", "1mo", operation])
    runpy.run_path(str(Path("nexus/core/studio_finance_worker.py")), run_name="__main__")
    result = json.loads(capsys.readouterr().out)
    if operation == "price":
        assert result["info"]["regularMarketPrice"] == 125.5 and "trailingPE" not in result["info"]
    else:
        assert (
            result["info"]["shortName"] == "Example Company"
            and "regularMarketPrice" not in result["info"]
        )
    assert "PRIVATE_EXTRA" not in result["info"] and "longBusinessSummary" not in result["info"]
    assert calls == ["EXAMPLE"]


def test_default_subprocess_boundary_is_bounded_and_does_not_inherit_credentials(
    tmp_path, monkeypatch
):
    calls = []
    studio = StudioService(tmp_path, research_options={"config": {"finance": True}})
    monkeypatch.setattr("nexus.core.studio_research.importlib.util.find_spec", lambda _: object())
    monkeypatch.setenv("PRIVATE_ACCOUNT_TOKEN", "must-not-inherit")

    def worker(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps({"info": INFO}).encode())

    monkeypatch.setattr("nexus.core.studio_research.subprocess.run", worker)
    try:
        assert (
            studio.research.finance({"ticker": "EXAMPLE", "operation": "price"})["current_price"]
            == 125.5
        )
        command, options = calls[0]
        assert command[-1] == "price" and "-I" in command
        assert options["timeout"] == 40 and set(options["env"]) == {"PATH", "HOME"}
        assert "PRIVATE_ACCOUNT_TOKEN" not in options["env"]

        def failed(*args, **kwargs):
            raise subprocess.TimeoutExpired("PRIVATE_PROVIDER_DETAIL", 40)

        monkeypatch.setattr("nexus.core.studio_research.subprocess.run", failed)
        with pytest.raises(ValueError, match="no trading action") as error:
            studio.research.finance({"ticker": "EXAMPLE", "operation": "fundamentals"})
        assert "PRIVATE_PROVIDER_DETAIL" not in str(error.value)
    finally:
        studio.close()


def test_actual_workflow_and_http_example_keep_both_operations_and_pinned_run_output(tmp_path):
    example = runpy.run_path("examples/nexus-bot/src/studio_finance.py")
    studio = StudioService(
        tmp_path,
        research_options={
            "config": {"finance": True},
            "finance_info_client": lambda _: dict(example["INFO"]),
        },
    )
    try:
        output = example["exercise"](studio)
        assert output["quote"]["current_price"] == 125.5
        assert output["company"]["pe_ratio"] == 20
    finally:
        studio.close()


def test_operation_readiness_is_passive_and_distinguishes_injected_capabilities(
    tmp_path, monkeypatch
):
    studio = StudioService(
        tmp_path,
        research_options={
            "config": {"finance": True},
            "finance_info_client": lambda _: pytest.fail("Readiness cannot query market data"),
        },
    )
    monkeypatch.setattr("nexus.core.studio_research.importlib.util.find_spec", lambda _: None)
    try:
        item = next(
            row for row in studio.research.inventory()["items"] if row["id"].endswith(":finance")
        )
        assert item["configured"]
        assert item["operations"] == {"history": False, "price": True, "fundamentals": True}
        studio.research.config["finance"] = False
        item = next(
            row for row in studio.research.inventory()["items"] if row["id"].endswith(":finance")
        )
        assert not item["configured"] and not any(item["operations"].values())
    finally:
        studio.close()
