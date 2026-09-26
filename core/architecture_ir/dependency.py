"""What a connection means: its kind, how the two sides interact, and over which protocol.

Direction: ``source`` is the side that initiates (the caller, the publisher, the consumer pulling
from a broker, the service reading a database); ``target`` the side it reaches. Traffic flowing
both ways over one connection (a websocket) is ``bidirectional``, not two connections.

A **dependency** is not communication: the source needs the target to work (configuration, DNS,
a secret store read at start) without exchanging application traffic with it. It has no protocol
and no interaction.
"""

import re
from enum import StrEnum


class ConnectionKind(StrEnum):
    REQUEST = "request"  # a call expecting a response: HTTP, gRPC
    PUBLISH = "publish"  # the source publishes messages or events to the target (a broker)
    CONSUME = "consume"  # the source consumes messages from the target (a broker)
    DATA_ACCESS = "data_access"  # the source reads or writes data held by the target
    REPLICATION = "replication"  # the target receives a copy of the source's data
    DEPENDENCY = "dependency"  # the source needs the target, without communicating with it

    @property
    def communicates(self) -> bool:
        return self is not ConnectionKind.DEPENDENCY


class Interaction(StrEnum):
    SYNCHRONOUS = "synchronous"  # the source waits for the target
    ASYNCHRONOUS = "asynchronous"  # the source does not wait


# "https", "grpc", "postgresql", "redis", "kafka", "amqp", "s3", "tcp": identifiers, not a closed list.
PROTOCOL = re.compile(r"^[a-z0-9][a-z0-9+._-]{0,31}$")
