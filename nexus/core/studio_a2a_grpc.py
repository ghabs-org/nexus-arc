"""Opt-in generated A2A gRPC binding over the existing authorized task service."""

from concurrent.futures import ThreadPoolExecutor

from nexus.core.studio_a2a_http import error_response
from nexus.core.studio_a2a_server import A2AError


class StudioA2AGRPC:
    def __init__(self, studio, entrypoint_id, *, host="127.0.0.1", port=0, credentials=None):
        try:
            import grpc
            from a2a.types import a2a_pb2 as messages
            from a2a.types import a2a_pb2_grpc as service
            from google.protobuf.json_format import MessageToDict, ParseDict
            from google.rpc import error_details_pb2, status_pb2
        except ImportError:
            raise ValueError(
                "Install nexus-arc[studio-grpc] to start the optional A2A listener"
            ) from None
        if host not in {"127.0.0.1", "localhost", "::1"} and credentials is None:
            raise ValueError("Non-loopback gRPC listeners require TLS server credentials")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Choose a valid gRPC port")
        self.studio, self.identity, self.closed = studio, entrypoint_id, False
        studio.catalog.get("entrypoints", entrypoint_id)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nexus-a2a-grpc")
        self.server = grpc.server(
            self.pool,
            options=[
                ("grpc.max_receive_message_length", 24 * 1024 * 1024),
                ("grpc.max_send_message_length", 32 * 1024 * 1024),
            ],
        )
        adapter = self

        def abort(context, error=None, status=None, message=None):
            _, body = error_response(error, status=status, message=message)
            value = body["error"]
            label = value["status"]
            code = getattr(grpc.StatusCode, label, grpc.StatusCode.INVALID_ARGUMENT)
            detail = status_pb2.Status(code=code.value[0], message=value["message"])
            for info in value["details"]:
                detail.details.add().Pack(
                    error_details_pb2.ErrorInfo(reason=info["reason"], domain=info["domain"])
                )
            context.set_trailing_metadata(
                (("grpc-status-details-bin", detail.SerializeToString()),)
            )
            context.abort(code, value["message"])

        def execute(request, context, operation):
            metadata = dict(context.invocation_metadata())
            bearer = metadata.get("authorization", "").removeprefix("Bearer ")
            try:
                record = studio.a2a.authorize(entrypoint_id, bearer)
                version = studio.a2a.version(metadata.get("a2a-version") or "1.0")
                if version != "1.0":
                    raise A2AError(-32009, "The gRPC binding supports A2A 1.0")
                params = MessageToDict(request)
                if params.get("tenant") not in {None, "", entrypoint_id}:
                    raise A2AError(-32004, "This listener serves one configured entrypoint")
                if operation == "push":
                    raise A2AError(
                        -32003, "Push notifications are not offered; use task subscriptions"
                    )
                origin = studio.team.public_url if studio.team else "http://localhost"
                result, streaming = studio.a2a.invoke(
                    record,
                    operation,
                    params,
                    "1.0",
                    origin,
                    wait_active=lambda: context.is_active() and not adapter.closed,
                )
                studio.a2a.authorize(entrypoint_id, bearer)
                return record, bearer, result, streaming
            except PermissionError:
                abort(context, status=403, message="A2A entrypoint authorization required")
            except FileNotFoundError:
                abort(context, status=404, message="A2A entrypoint not found")
            except (ValueError, TypeError, KeyError) as error:
                abort(context, error)

        def unary(operation, output):
            def handle(request, context):
                _, _, result, _ = execute(request, context, operation)
                try:
                    return ParseDict(result, output())
                except (ValueError, TypeError):
                    abort(context, A2AError(-32006, "Task response does not match the A2A schema"))

            return handle

        def stream(operation):
            def handle(request, context):
                record, bearer, result, _ = execute(request, context, operation)
                try:
                    for event in studio.a2a.updates(
                        record,
                        result,
                        "1.0",
                        bearer,
                        active=lambda: context.is_active() and not adapter.closed,
                    ):
                        if not context.is_active():
                            return
                        yield ParseDict(event, messages.StreamResponse())
                except PermissionError:
                    abort(context, status=403, message="A2A entrypoint authorization required")
                except (ValueError, TypeError):
                    abort(context, A2AError(-32006, "Task response does not match the A2A schema"))

            return handle

        handlers = type("SharedTaskServicer", (service.A2AServiceServicer,), {})()
        bindings = {
            "SendMessage": ("send", messages.SendMessageResponse),
            "GetTask": ("get", messages.Task),
            "ListTasks": ("list", messages.ListTasksResponse),
            "CancelTask": ("cancel", messages.Task),
            "GetExtendedAgentCard": ("card", messages.AgentCard),
            "CreateTaskPushNotificationConfig": ("push", messages.TaskPushNotificationConfig),
            "GetTaskPushNotificationConfig": ("push", messages.TaskPushNotificationConfig),
            "ListTaskPushNotificationConfigs": (
                "push",
                messages.ListTaskPushNotificationConfigsResponse,
            ),
            "DeleteTaskPushNotificationConfig": ("push", None),
        }
        for name, (operation, output) in bindings.items():
            setattr(handlers, name, unary(operation, output))
        handlers.SendStreamingMessage = stream("stream")
        handlers.SubscribeToTask = stream("subscribe")
        service.add_A2AServiceServicer_to_server(handlers, self.server)
        address = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        with studio.lock:
            if entrypoint_id in studio.a2a.grpc_servers:
                self.pool.shutdown(wait=False)
                raise ValueError("A gRPC listener already serves this entrypoint")
            try:
                selected = (
                    self.server.add_secure_port(address, credentials)
                    if credentials
                    else self.server.add_insecure_port(address)
                )
                if not selected:
                    raise ValueError("gRPC listener could not bind its address")
                self.address = f"[{host}]:{selected}" if ":" in host else f"{host}:{selected}"
                self.interface = {
                    "url": self.address,
                    "protocolBinding": "GRPC",
                    "protocolVersion": "1.0",
                }
                self.server.start()
                studio.a2a.grpc_servers[entrypoint_id] = adapter
            except Exception:
                self.server.stop(0)
                self.pool.shutdown(wait=False)
                raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        with self.studio.lock:
            if self.studio.a2a.grpc_servers.get(self.identity) is self:
                del self.studio.a2a.grpc_servers[self.identity]
        self.server.stop(0).wait(timeout=5)
        self.pool.shutdown(wait=True, cancel_futures=True)
