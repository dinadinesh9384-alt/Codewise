"""Explicit proof that a workflow saver is suitable for human-approval resume."""

from dataclasses import dataclass, field
import uuid


@dataclass(frozen=True)
class CheckpointCapability:
    """Trusted runtime evidence for a schema-verified PostgreSQL checkpointer.

    Instances are created by the runtime composition root only after constructing
    the supported saver and verifying its PostgreSQL tables are accessible.
    """

    runtime_id: uuid.UUID
    checkpointer: object = field(repr=False, compare=False)
    database_engine: str
    saver_kind: str
    dependencies_verified: bool
    schema_verified: bool
    stable_thread_identity: str = "execution_uuid"

    def __post_init__(self):
        if not isinstance(self.runtime_id, uuid.UUID):
            raise TypeError("Checkpoint capability requires a runtime UUID.")
        if self.checkpointer is None:
            raise ValueError("Checkpoint capability requires a constructed saver.")
        if self.database_engine != "django.db.backends.postgresql":
            raise ValueError("Durable approval checkpoints require PostgreSQL.")
        if self.saver_kind != "langgraph.checkpoint.postgres.PostgresSaver":
            raise ValueError("Durable approval checkpoints require the supported PostgreSQL saver.")
        if self.dependencies_verified is not True or self.schema_verified is not True:
            raise ValueError("Checkpoint dependencies and schema must be verified.")
        if self.stable_thread_identity != "execution_uuid":
            raise ValueError("Checkpoint threads must use execution UUID identity.")

    def authorizes(self, *, checkpointer, runtime_id) -> bool:
        actual_saver = (type(checkpointer).__module__ == "langgraph.checkpoint.postgres" and
                        type(checkpointer).__name__ == "PostgresSaver")
        return (actual_saver and checkpointer is self.checkpointer and runtime_id == self.runtime_id and
                self.database_engine == "django.db.backends.postgresql" and
                self.saver_kind == "langgraph.checkpoint.postgres.PostgresSaver" and
                self.dependencies_verified is True and self.schema_verified is True and
                self.stable_thread_identity == "execution_uuid")
