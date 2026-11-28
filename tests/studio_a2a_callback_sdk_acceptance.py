"""Optional A2A SDK schemas; run explicitly after installing a2a-sdk[grpc]."""

import pytest

from tests.test_studio_a2a_http import gateway as gateway
from tests.test_studio_external_callbacks import receiving as receiving
from tests.test_studio_external_content import peer as peer


@pytest.mark.parametrize("version", ["1.0", "0.3"])
def test_emitted_callback_configuration_matches_independent_schema(receiving, version):
    _, _, config, start, _, _ = receiving
    start(version)
    wire = {k: v for k, v in config.items() if k != "early"}
    if version == "1.0":
        from a2a.types import a2a_pb2
        from google.protobuf.json_format import ParseDict

        parsed = ParseDict({"taskPushNotificationConfig": wire}, a2a_pb2.SendMessageConfiguration())
        assert parsed.task_push_notification_config.authentication.scheme == "Bearer"
    else:
        from a2a.compat.v0_3.types import PushNotificationConfig

        parsed = PushNotificationConfig.model_validate(wire)
        assert parsed.authentication.schemes == ["Bearer"]
