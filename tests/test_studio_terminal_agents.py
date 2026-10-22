"""Directed-message selection preserves literal bodies and ambiguity checks."""

import pytest

from nexus.core.studio_terminal_agents import agent_choices, directed_message


class Client:
    def request(self, path, data=None):
        assert path == "/api/agents" and data is None
        return {
            "items": [
                {"id": "reviewer", "document": {"metadata": {"name": "Review Agent"}}},
                {"id": "other", "document": {"metadata": {"name": "reviewer"}}},
            ]
        }


@pytest.mark.parametrize(
    "body",
    [
        '{"key": "value"}',
        "first\nsecond",
        'a "partial quote',
        "# literal comment",
        "a 'quoted' value",
    ],
)
def test_message_body_remains_literal_and_id_wins_over_name(body):
    assert directed_message("/agent reviewer " + body, Client()) == ("reviewer", body)


def test_quoted_name_and_unterminated_selector():
    assert directed_message('/agent "Review Agent" Hello', Client()) == ("reviewer", "Hello")
    with pytest.raises(ValueError):
        directed_message('/agent "Review Agent', Client())


def test_structurally_invalid_definitions_remain_listable_but_not_selectable():
    class BrokenClient:
        def request(self, path, data=None):
            return {"items": [{"id": "broken", "document": {"metadata": [], "spec": None}}]}

    assert agent_choices(BrokenClient())[0]["invalid"]
    with pytest.raises(ValueError, match="definition is invalid"):
        directed_message("/agent broken Hello", BrokenClient())
