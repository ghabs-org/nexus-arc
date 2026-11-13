"""Jev client: typed parsing, confidence gating, safe defaults."""


def _fake_response(payload):
    from unittest.mock import MagicMock

    response = MagicMock()
    response.json = MagicMock(return_value=payload)
    response.raise_for_status = lambda: None
    return response


def test_parse_choice_prefers_answer():
    from nexus.adapters.decisions.jev import parse_choice

    result = parse_choice(
        {"choice": "billing", "probabilities": {"billing": 0.88, "tech": 0.12},
         "confidence": 0.81},
        default="tech",
    )
    assert (result.choice, result.confidence) == ("billing", 0.81)


def test_parse_choice_falls_back_on_garbage():
    from nexus.adapters.decisions.jev import parse_choice

    assert parse_choice(None, default="tech").choice == "tech"
    assert parse_choice(None, default="tech").confidence == 0.0


def test_decide_choice_gates_low_confidence(monkeypatch):
    from unittest.mock import MagicMock

    import nexus.adapters.decisions.jev as _jev

    fake = MagicMock()
    fake.post = MagicMock(
        return_value=_fake_response(
            {"answers": {"route": {
                "choice": "billing",
                "probabilities": {"billing": 0.4, "tech": 0.6},
                "confidence": 0.3,
            }}}
        )
    )
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    choice, confidence, sure = _jev.decide_choice(
        "hi", {"billing": "b", "tech": "t"}, "Which?", min_confidence=0.6, default="tech"
    )
    assert (choice, sure) == ("tech", False)
    assert confidence == 0.3


def test_ask_returns_empty_without_key(monkeypatch):
    from nexus.adapters.decisions import jev as _jev

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert _jev.ask("state", {"q": {"type": "noul"}}) == {}
    choice, _, sure = _jev.decide_choice("s", {"a": "A"}, "Pick?", default="a")
    assert (choice, sure) == ("a", False)


def test_backend_prefers_free_opencode(monkeypatch):
    from nexus.adapters.decisions.jev import resolve_backend

    monkeypatch.delenv("JEV_BACKEND", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_API_KEY", "oc-test")
    endpoint, key, model = resolve_backend()
    assert endpoint == "https://opencode.ai/zen/v1/systemone"
    assert (key, model) == ("oc-test", "jev-1.13-free")


def test_backend_openrouter_when_only_key(monkeypatch):
    from nexus.adapters.decisions.jev import resolve_backend

    monkeypatch.delenv("JEV_BACKEND", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    endpoint, key, model = resolve_backend()
    assert endpoint == "https://openrouter.ai/api/alpha/decisions"
    assert (key, model) == ("or-test", "typesafe/jev-1.13")


def test_backend_none_when_unkeyed(monkeypatch):
    from nexus.adapters.decisions.jev import resolve_backend

    monkeypatch.delenv("JEV_BACKEND", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert resolve_backend() is None
