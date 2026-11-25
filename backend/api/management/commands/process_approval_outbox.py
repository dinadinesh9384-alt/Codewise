"""Process pending or expired human approval outbox commands."""

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from api.v1.approval_outbox import process_approval_outbox
from apps.repositories.models import ApprovalOutboxEvent


class Command(BaseCommand):
    help = "Process durable human approval commands and reconcile advanced checkpoints."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 500:
            self.stderr.write("--limit must be between 1 and 500.")
            return
        now = timezone.now()
        event_ids = list(ApprovalOutboxEvent.objects.filter(
            Q(status__in=(ApprovalOutboxEvent.Status.PENDING, ApprovalOutboxEvent.Status.RETRYABLE)) |
            Q(status=ApprovalOutboxEvent.Status.PROCESSING, lease_until__lte=now)
        ).order_by("created_at").values_list("id", flat=True)[:limit])
        completed = sum(process_approval_outbox(event_id) is not None for event_id in event_ids)
        self.stdout.write(f"Processed {completed} of {len(event_ids)} approval outbox events.")
