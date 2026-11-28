"""Secret masking filter plus key omission for log/API boundaries."""


def test_omit_secret_keys_drops_not_masks():
    from nexus.core.utils.logging_filters import omit_secret_keys

    cleaned = omit_secret_keys(
        {"OPENAI_API_KEY": "sk-live", "NEXUS_MODE": "chat", "CUSTOM": "x"},
        extra_keys=["CUSTOM"],
    )
    assert cleaned == {"NEXUS_MODE": "chat"}


def test_omit_secret_keys_non_mapping():
    from nexus.core.utils.logging_filters import omit_secret_keys

    assert omit_secret_keys(None) == {}
    assert omit_secret_keys("sk-live") == {}


def test_masking_filter_still_redacts_values():
    import logging

    from nexus.core.utils.logging_filters import SecretRedactingFilter

    filt = SecretRedactingFilter(["sk-live"])
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "key=%s", ("sk-live",), None)
    assert filt.filter(record) is True
    assert record.args == ("[REDACTED_SECRET]",)
