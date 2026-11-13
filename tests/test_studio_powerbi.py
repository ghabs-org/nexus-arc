"""Power BI HTTP success never hides query errors or partial result tables."""

from copy import deepcopy

import pytest

from nexus.core.studio_enterprise import validate_powerbi_response

DATASET = "cfafbeb1-8037-4d0c-896e-a46fb27ff229"


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {},
        {"error": {}},
        {"results": []},
        {"results": [{}, {}]},
        {"results": [None]},
        {"results": [{"error": {"message": "PRIVATE_DETAIL"}}]},
        {"results": [{"tables": []}]},
        {"results": [{"tables": [{}, {}]}]},
        {"results": [{"tables": [None]}]},
        {"results": [{"tables": [{"rows": [], "error": {"message": "PRIVATE_DETAIL"}}]}]},
        {"results": [{"tables": [{"rows": "bad"}]}]},
        {"results": [{"tables": [{"rows": [1]}]}]},
    ],
)
def test_invalid_and_partial_query_response_rejected(response):
    with pytest.raises(ValueError) as error:
        validate_powerbi_response(response, DATASET, "query")
    assert "PRIVATE_DETAIL" not in str(error.value)


@pytest.mark.parametrize("rows", [[], [{"[value]": None, "Table[qualified]": 42}]])
def test_valid_query_preserves_raw_shape_and_labels(rows):
    value = {
        "results": [{"tables": [{"rows": rows}]}],
        "informationProtectionLabel": {"name": "Private"},
    }
    original = deepcopy(value)
    assert validate_powerbi_response(value, DATASET, "query") is value
    assert value == original


@pytest.mark.parametrize(
    "value", [{}, {"id": "wrong"}, {"id": DATASET, "error": {"message": "private"}}, {"id": 12}]
)
def test_dataset_metadata_must_match_selected_identity(value):
    with pytest.raises(ValueError):
        validate_powerbi_response(value, DATASET, "read")


def test_metadata_identity_is_case_insensitive_and_preserves_optional_fields():
    value = {"id": DATASET.upper(), "name": "Revenue", "isRefreshable": True}
    assert validate_powerbi_response(value, DATASET, "read") is value
