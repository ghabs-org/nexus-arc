# Optional A2A 1.0 gRPC binding

Wire schema: official `a2a-sdk[grpc]==1.1.5`, `lf.a2a.v1.A2AService`.
Uses generated protobuf messages and service registration, not JSON sent over a
custom gRPC method. The same `StudioA2AServer.invoke` handles SendMessage,
SendStreamingMessage, GetTask, ListTasks, CancelTask, SubscribeToTask and
GetExtendedAgentCard. Push configuration operations return the canonical
FAILED_PRECONDITION / PUSH_NOTIFICATION_NOT_SUPPORTED error after authorization.

Each explicitly started listener serves one existing entrypoint. Every request
requires its bearer in gRPC `authorization` metadata and passes original-creator
execution checks. A nonempty tenant must match that entrypoint. Authorization is
rechecked after unary execution and during streamed delivery. Disconnecting a
subscriber does not submit/cancel a task. Standard grpc-status-details-bin carries
serialized google.rpc.Status with A2A ErrorInfo reasons.

Default listener is loopback only. A non-loopback bind requires caller-supplied
gRPC TLS server credentials. Message reception is bounded to 24 MiB, replies to
32 MiB, with four worker threads. Startup/stop updates runtime discovery; no gRPC
interface is advertised merely because the optional package is installed.
Studio shutdown closes registered listeners. No persisted record rewrite.

Primary references: https://a2a-protocol.org/latest/specification/#10-grpc-protocol-binding
and https://pypi.org/project/a2a-sdk/1.1.5/ (inspected 19 October 2026).
