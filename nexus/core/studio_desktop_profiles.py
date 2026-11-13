"""Fixed desktop dependency profiles; no installation or provider activation."""

import importlib
import importlib.metadata

# Distribution names also drive PyInstaller metadata collection. Import targets
# exercise dynamic driver paths rather than treating a discoverable package as usable.
CONNECTED = {
    "openai": "openai",
    "anthropic": "anthropic",
    "mcp": "mcp.client.streamable_http",
    "slack-sdk": "slack_sdk.web.client",
    "sqlalchemy": "sqlalchemy",
    "psycopg2-binary": "psycopg2",
    "pymysql": "pymysql",
    "pymssql": "pymssql",
    "oracledb": "oracledb",
    "jsonschema": "jsonschema",
    "referencing": "referencing",
    "boto3": "boto3",
    "pymongo": "pymongo",
    "pypdf": "pypdf",
    "Pillow": "PIL.Image",
    "httpx": "httpx",
    "cryptography": "cryptography.hazmat.primitives.ciphers.aead",
}
FULL = {
    **CONNECTED,
    "yfinance": "yfinance",
    "google-cloud-storage": "google.cloud.storage",
    "azure-storage-blob": "azure.storage.blob",
    "a2a-sdk": "a2a.types.a2a_pb2_grpc",
    "grpcio": "grpc",
    "googleapis-common-protos": "google.rpc.error_details_pb2",
    "protobuf": "google.protobuf.json_format",
    "python-telegram-bot": "telegram.ext",
    "discord.py": "discord",
    "redis": "redis",
    "paho-mqtt": "paho.mqtt.client",
    "kafka-python": "kafka",
    "rich": "rich.console",
    "prompt-toolkit": "prompt_toolkit",
    "openai-whisper": "whisper",
    "torch": "torch",
    "tiktoken": "tiktoken_ext.openai_public",
    "watchdog": "watchdog.observers",
    "slack-bolt": "slack_bolt",
    "python-dotenv": "dotenv",
    "flask": "flask",
    "flask-socketio": "flask_socketio",
    "eventlet": "eventlet",
    "websocket-client": "websocket",
    "apscheduler": "apscheduler.schedulers.asyncio",
    "PyNaCl": "nacl.secret",
    "aiohttp": "aiohttp",
}
PROFILES = {"core": {}, "connected": CONNECTED, "full": FULL}


def profile_requirements(name):
    if name not in PROFILES:
        raise ValueError("Unknown desktop dependency profile")
    return dict(PROFILES[name])


def check_profile(name):
    """Import fixed SDK targets without constructing clients or reading credentials."""
    results = {}
    for distribution, module in profile_requirements(name).items():
        try:
            version = importlib.metadata.version(distribution)
            importlib.import_module(module)
            results[distribution] = {"module": module, "version": version, "imported": True}
        except Exception as error:
            # Only the failure class is public: imported SDK exceptions can carry
            # environment-specific paths or values. This is not a live API check.
            results[distribution] = {
                "module": module,
                "imported": False,
                "error_type": type(error).__name__,
            }
    checks = {}
    if name == "full" and all(row["imported"] for row in results.values()):
        for label, check in full_offline_checks().items():
            try:
                check()
                checks[label] = {"passed": True}
            except Exception as error:
                checks[label] = {"passed": False, "error_type": type(error).__name__}
    return {
        "format": "nexus-desktop-profile/v1",
        "profile": name,
        "passed": all(row["imported"] for row in results.values())
        and all(row["passed"] for row in checks.values()),
        "packages": results,
        "offline_checks": checks,
        "scope": "Offline imports and packaged-data checks only; not account, model or service validation",
    }


def full_offline_checks():
    """Fixed local operations; never create clients, load models or request URLs."""

    def speech_data():
        from whisper.audio import mel_filters
        from whisper.tokenizer import get_tokenizer

        if tuple(mel_filters("cpu", 80).shape) != (80, 201):
            raise ValueError("Invalid local speech filter data")
        tokenizer = get_tokenizer(multilingual=True, language="en")
        if tokenizer.decode(tokenizer.encode("Nexus Studio")) != "Nexus Studio":
            raise ValueError("Invalid local speech tokenizer data")

    def cloud_models():
        from botocore.session import get_session

        session = get_session()
        for name in ["bedrock-agent-runtime", "s3"]:
            if not session.get_service_model(name).operation_names:
                raise ValueError("Cloud service descriptions unavailable")

    def grpc_messages():
        from a2a.types import a2a_pb2

        value = a2a_pb2.Task(id="offline-package-check")
        if a2a_pb2.Task.FromString(value.SerializeToString()).id != value.id:
            raise ValueError("Invalid local protocol messages")

    return {
        "speech_data": speech_data,
        "cloud_models": cloud_models,
        "grpc_messages": grpc_messages,
    }
