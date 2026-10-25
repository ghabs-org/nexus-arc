"""Slack Socket Mode integration for inbound event and interaction streaming."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from slack_sdk import WebClient
    from slack_sdk.socket_mode.builtin import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    _SOCKET_MODE_AVAILABLE = True
except ImportError:
    _SOCKET_MODE_AVAILABLE = False
    SocketModeClient = object  # type: ignore
    SocketModeRequest = object  # type: ignore
    SocketModeResponse = object  # type: ignore


class StudioSlackSocketMode:
    """Manages a WebSocket connection to Slack via Socket Mode."""

    def __init__(
        self,
        studio: Any,
        app_token: str,
        bot_token: str | None = None,
        on_event: Callable[[str, dict[str, Any]], Any] | None = None,
        custom_client: Any = None,
    ) -> None:
        if not _SOCKET_MODE_AVAILABLE and custom_client is None:
            raise ImportError(
                "slack-sdk with Socket Mode is required. "
                "Install it with: pip install nexus-arc[slack]"
            )
        if not app_token or not app_token.startswith("xapp-"):
            raise ValueError("Slack Socket Mode requires a valid App Token starting with 'xapp-'")

        self.studio = studio
        self.app_token = app_token
        self.bot_token = bot_token or ""
        self.on_event = on_event
        self._connected = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        if custom_client is not None:
            self._client = custom_client
        else:
            web_client = WebClient(token=self.bot_token, retry_handlers=[]) if self.bot_token else None
            self._client = SocketModeClient(
                app_token=self.app_token,
                web_client=web_client,
            )

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _handle_socket_mode_request(self, client: Any, req: SocketModeRequest) -> None:
        """Process incoming Socket Mode request and immediately acknowledge."""
        try:
            # 1. Immediate acknowledgment envelope
            response = SocketModeResponse(envelope_id=req.envelope_id)
            client.send_socket_mode_response(response)

            req_type = getattr(req, "type", "")
            payload = getattr(req, "payload", {})

            # 2. Dispatch to custom handler if registered
            if self.on_event:
                self.on_event(req_type, payload)

            # 3. Route to studio messaging
            if hasattr(self.studio, "messaging"):
                self.studio.messaging.handle_channel_event(
                    "slack",
                    payload,
                    headers={"x-slack-signature": "socket_mode", "x-slack-request-timestamp": str(int(time.time()))},
                )
        except Exception as exc:
            logger.exception("Error processing Slack Socket Mode envelope: %s", exc)

    def connect(self) -> bool:
        """Connect to Slack Socket Mode."""
        if self._connected:
            return True

        if hasattr(self._client, "socket_mode_request_listeners"):
            self._client.socket_mode_request_listeners.append(self._handle_socket_mode_request)

        if hasattr(self._client, "connect"):
            self._client.connect()

        self._connected = True
        return True

    def start_background(self) -> None:
        """Start Socket Mode client in a managed background thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self.connect, daemon=True, name="SlackSocketModeThread")
        self._thread.start()

    def disconnect(self) -> None:
        """Disconnect and stop client cleanly."""
        self._stop_event.set()
        self._connected = False
        if hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:
                pass
        if hasattr(self._client, "disconnect"):
            try:
                self._client.disconnect()
            except Exception:
                pass

    def close(self) -> None:
        """Alias for disconnect."""
        self.disconnect()
