"""Provision the single initial CodeWise workspace for an existing superuser."""
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, OperationalError, transaction

from apps.repositories.models import User, Workspace, WorkspaceMembership
from apps.repositories.services import create_workspace


class Command(BaseCommand):
    help = "Create the initial workspace and assign its owner."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Existing active Django superuser username.")
        parser.add_argument("--name", required=True, help="Initial workspace name.")
        parser.add_argument("--description", default="", help="Optional workspace description.")

    def handle(self, *args, **options):
        username = options["username"]
        name = options["name"].strip()
        description = options["description"].strip()
        if not name:
            raise CommandError("Workspace name cannot be blank.")

        try:
            result = self._provision(username=username, name=name, description=description)
        except IntegrityError:
            # The conditional unique constraint is the cross-process guard. Resolve a
            # concurrent identical winner as idempotent; reject every other outcome.
            if self._matches_existing(username=username, name=name, description=description):
                self.stdout.write(self.style.SUCCESS("Initial workspace was already provisioned."))
                return
            raise CommandError("Concurrent provisioning created an ambiguous state; inspect the initial workspace before retrying.") from None
        except OperationalError:
            # SQLite may reject a competing writer instead of waiting on a row lock.
            # The transaction rolls back, so retrying after the other command exits is safe.
            raise CommandError("A concurrent provisioning attempt is in progress; retry after it finishes.") from None

        if result == "existing":
            self.stdout.write(self.style.SUCCESS("Initial workspace was already provisioned."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Created initial workspace '{name}' and assigned owner '{username}'."))

    def _provision(self, *, username, name, description):
        with transaction.atomic():
            # Serialize provisioning commands on databases that support row locks,
            # including attempts targeting different superusers.
            User.objects.select_for_update().filter(is_superuser=True).order_by("pk").first()
            try:
                user = User.objects.select_for_update().get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"Django user '{username}' does not exist.") from None

            if not user.is_active:
                raise CommandError(f"Django user '{username}' is inactive.")
            if not user.is_superuser:
                raise CommandError(f"Django user '{username}' is not a superuser.")

            initial = Workspace.objects.select_for_update().filter(is_initial=True).first()
            memberships = WorkspaceMembership.objects.filter(user=user).select_related("workspace")
            if initial is not None:
                other_memberships = memberships.exclude(workspace_id=initial.pk)
                if other_memberships.exists():
                    raise CommandError(f"Django user '{username}' already belongs to another workspace; provisioning is ambiguous.")
                owner_membership = memberships.filter(workspace_id=initial.pk).first()
                if (initial.name != name or initial.description != description or owner_membership is None
                        or owner_membership.role != WorkspaceMembership.Role.OWNER):
                    raise CommandError("An initial workspace already exists but does not match this request and owner; no changes were made.")
                return "existing"

            if memberships.exists():
                raise CommandError(f"Django user '{username}' already belongs to another workspace; provisioning is ambiguous.")
            if Workspace.objects.exists():
                raise CommandError("Workspace records already exist without an initial workspace marker; provisioning is ambiguous.")

            create_workspace(owner=user, name=name, description=description, is_initial=True)
            return "created"

    @staticmethod
    def _matches_existing(*, username, name, description):
        initial = Workspace.objects.filter(is_initial=True).first()
        if initial is None or initial.name != name or initial.description != description:
            return False
        return WorkspaceMembership.objects.filter(
            workspace=initial, user__username=username, user__is_active=True,
            user__is_superuser=True, role=WorkspaceMembership.Role.OWNER,
        ).exists()
