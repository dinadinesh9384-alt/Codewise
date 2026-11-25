"""Explicitly initialize LangGraph Postgres checkpoint tables."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from config.workflow_runtime import WorkflowRuntimeConfigurationError, open_checkpoint_pool


class Command(BaseCommand):
    help = "Create or upgrade LangGraph checkpoint tables in the configured PostgreSQL database."

    def handle(self, *args, **options):
        if not settings.CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED:
            raise CommandError(
                "Set CODEWISE_LANGGRAPH_CHECKPOINTS_ENABLED=true before initializing checkpoint storage."
            )
        pool = None
        try:
            pool, saver, _capability = open_checkpoint_pool(verify_schema=False)
            saver.setup()
        except WorkflowRuntimeConfigurationError:
            raise CommandError("PostgreSQL checkpoint storage is unavailable.") from None
        except Exception:
            raise CommandError("Checkpoint storage initialization failed.") from None
        finally:
            if pool is not None:
                pool.close()
        self.stdout.write(self.style.SUCCESS("LangGraph checkpoint tables are initialized."))
