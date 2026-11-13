"""Run the actual yfinance SDK/worker over local Postman HTTP response fixtures.

Requires an isolated environment with yfinance==0.2.65 and a running
postman/simulations/studio-finance.sim.yaml. No provider accounts are used.
Only the SDK's URL transport and cookie bootstrap are redirected for this test;
Ticker.get_info, quote parsing, worker field filtering and normalization run unchanged.
"""

import json
import runpy
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

from nexus.core.studio_finance import stock_result


def exercise(base):
    import yfinance
    from curl_cffi import requests
    from yfinance.data import YfData

    url = urlparse(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("Use the local finance contract simulation")
    if yfinance.__version__ != "0.2.65":
        raise ValueError("This SDK contract acceptance pins yfinance==0.2.65")
    original_ticker = yfinance.Ticker
    original_cookie = YfData._get_cookie_and_crumb
    original_argv = sys.argv

    class LocalSession(requests.Session):
        def get(self, url, *args, **kwargs):
            remote = urlparse(url)
            if remote.hostname not in {"query1.finance.yahoo.com", "query2.finance.yahoo.com"}:
                raise AssertionError("SDK attempted an unmodeled external destination")
            if any(key.lower() in {"authorization", "cookie"} for key in kwargs.get("headers", {})):
                raise AssertionError("Credentials must not enter SDK contract fixtures")
            kwargs["timeout"] = min(kwargs.get("timeout", 10), 10)
            return super().get(
                base.rstrip("/") + remote.path + ("?" + remote.query if remote.query else ""),
                *args,
                **kwargs,
            )

    sessions = []

    def ticker(symbol):
        session = LocalSession(impersonate="chrome", trust_env=False)
        sessions.append(session)
        return original_ticker(symbol, session=session)

    def invoke(symbol, operation):
        sys.argv = ["worker", symbol, "1mo", operation]
        output = StringIO()
        with redirect_stdout(output):
            runpy.run_path(
                str(Path(__file__).resolve().parents[1] / "nexus/core/studio_finance_worker.py"),
                run_name="__main__",
            )
        raw = json.loads(output.getvalue())
        assert "PRIVATE_UNSELECTED_FIELD" not in output.getvalue()
        return stock_result(symbol, operation, raw["info"])

    try:
        yfinance.Ticker = ticker
        YfData._get_cookie_and_crumb = lambda *args, **kwargs: (None, "basic")
        quote = invoke("EXAMPLE", "price")
        assert quote["current_price"] == 125.5 and quote["change_percent"] == 4.58
        fundamentals = invoke("EXAMPLE", "fundamentals")
        assert (
            fundamentals["company_name"] == "Example Company"
            and fundamentals["dividend_yield"] == 0
        )
        zero = invoke("ZERO", "price")
        assert zero["current_price"] == 0 and zero["change_percent"] is None
        for symbol, operation in [
            ("MISSING", "price"),
            ("MISSING", "fundamentals"),
            ("FAIL", "price"),
            ("BADJSON", "price"),
        ]:
            try:
                invoke(symbol, operation)
            except Exception:
                pass
            else:
                raise AssertionError("Malformed/missing provider results must not succeed")
        return {"sdk": "0.2.65", "passed": 7, "live_provider": False, "cookie_auth_verified": False}
    finally:
        yfinance.Ticker = original_ticker
        YfData._get_cookie_and_crumb = original_cookie
        sys.argv = original_argv
        for session in sessions:
            session.close()


if __name__ == "__main__":
    print(json.dumps(exercise(sys.argv[1])))
