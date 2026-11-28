"""Mail composition validation and review use the existing OpenAPI path."""

from copy import deepcopy

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_openapi import _validate, validate_openapi
from nexus.core.studio_recipes import SENDGRID_SPEC


def message():
    return {
        "personalizations": [{"to": [{"email": "recipient@example.invalid"}]}],
        "from": {"email": "sender@example.invalid"},
        "subject": "Welcome",
        "content": [{"type": "text/plain", "value": "Hello"}],
    }


def validate(body):
    schema = SENDGRID_SPEC["paths"]["/v3/mail/send"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    _validate(SENDGRID_SPEC, schema, body)


@pytest.mark.parametrize(
    "change",
    [
        {"personalizations": []},
        {"personalizations": [{}]},
        {"personalizations": [{"to": []}]},
        {"personalizations": [{"to": [{"email": "no-address"}]}]},
        {"personalizations": [{"to": [{"email": "a@b"}], "cc": [{"email": "bad"}]}]},
        {"from": {}},
        {"from": {"email": "line\nbreak@example.invalid"}},
        {"subject": ""},
        {"content": []},
        {"content": [{}]},
        {"content": [{"type": "text/html", "value": ""}]},
        {"template_id": ""},
    ],
)
def test_invalid_composition_rejected(change):
    with pytest.raises(ValueError, match="declared schema"):
        validate({**message(), **change})


def test_html_templates_and_personalization_subjects():
    body = message()
    body["content"] = [{"type": "text/html", "value": "<h1>Hello</h1>"}]
    body["tracking_settings"] = {"click_tracking": {"enable": False}}
    validate(body)
    del body["subject"]
    body["personalizations"][0]["subject"] = "Personal subject"
    validate(body)
    body["personalizations"].append({"to": [{"email": "other@example.invalid"}]})
    with pytest.raises(ValueError, match="declared schema"):
        validate(body)
    body.pop("content")
    body["template_id"] = "d-fixture"
    body["personalizations"][0]["dynamic_template_data"] = {"name": "Fixture"}
    validate(body)


def test_new_recipe_uses_versioned_bearer_contract_without_rewriting_existing(tmp_path):
    studio = StudioService(tmp_path)
    try:
        created = studio.recipes.configure("send-grid", {}, None)
        assert not created["data"]["enabled"]
        assert created["data"]["spec"]["info"]["version"] == "2"
        old = deepcopy(created)
        original = {
            "openapi": "3.0.3",
            "info": {"title": "SendGrid mail", "version": "1"},
            "paths": {
                "/v3/mail/send": {
                    "post": {
                        "operationId": "sendMail",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": [
                                            "personalizations",
                                            "from",
                                            "subject",
                                            "content",
                                        ],
                                        "properties": {
                                            "personalizations": {"type": "array", "minItems": 1},
                                            "from": {"type": "object"},
                                            "subject": {"type": "string"},
                                            "content": {"type": "array", "minItems": 1},
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"202": {"description": "Accepted"}},
                    }
                }
            },
        }
        old["data"]["spec"] = original
        old = studio.catalog.save("connectors", old)
        assert studio.catalog.get("connectors", old["id"])["data"]["spec"] == original
        wrong = deepcopy(created["data"])
        wrong["auth"] = {"type": "none"}
        with pytest.raises(ValueError):
            validate_openapi(wrong)
    finally:
        studio.close()
