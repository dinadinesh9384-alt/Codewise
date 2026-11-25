import uuid
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from pydantic import ValidationError as PydanticValidationError

from .contracts import RepositoryAccessState, RepositoryMetadata, RepositoryOperationResult, RepositoryIdentity, SnapshotMetadata
from .errors import LastWorkspaceOwnerError, RepositoryGrantRequired, SnapshotNotSealed, WorkspaceAccessDenied
from .models import AuditEvent, ExecutionSnapshotBinding, Repository, RepositoryGrant, RepositorySnapshot, RepositorySubmission, User, Workspace, WorkspaceMembership
from .services import change_membership_role, create_audit_event, create_execution_snapshot_binding, create_workspace, get_authorized_snapshot, list_audit_events, list_repository_grants, get_repository, remove_membership


class WorkspaceDomainTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="safe-test")
        self.member = User.objects.create_user(username="member", password="safe-test")
        self.workspace = create_workspace(owner=self.owner, name="Team")
        self.owner_membership = WorkspaceMembership.objects.get(workspace=self.workspace, user=self.owner)
        self.member_membership = WorkspaceMembership.objects.create(workspace=self.workspace, user=self.member)

    def test_workspace_creation_assigns_owner(self):
        self.assertEqual(self.owner_membership.role, WorkspaceMembership.Role.OWNER)

    def test_membership_roles_and_unique_member_constraint(self):
        self.assertEqual(self.member_membership.role, WorkspaceMembership.Role.MEMBER)
        with self.assertRaises(ValidationError):
            WorkspaceMembership.objects.create(workspace=self.workspace, user=self.member)

    def test_last_owner_cannot_be_demoted_or_removed(self):
        with self.assertRaises(LastWorkspaceOwnerError):
            change_membership_role(actor=self.owner, membership_id=self.owner_membership.pk, role="member")
        with self.assertRaises(LastWorkspaceOwnerError):
            remove_membership(actor=self.owner, membership_id=self.owner_membership.pk)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, "owner")

    def test_membership_queryset_bulk_mutations_are_rejected(self):
        with self.assertRaises(ValidationError):
            WorkspaceMembership.objects.filter(pk=self.member_membership.pk).update(role="owner")
        with self.assertRaises(ValidationError):
            WorkspaceMembership.objects.filter(pk=self.owner_membership.pk).delete()

    def test_owner_can_demote_when_another_owner_exists(self):
        second = User.objects.create_user(username="second", password="safe-test")
        WorkspaceMembership.objects.create(workspace=self.workspace, user=second, role="owner")
        change_membership_role(actor=self.owner, membership_id=self.owner_membership.pk, role="member")
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, "member")

    def test_workspace_scoping_denies_other_workspace_members(self):
        foreign_owner = User.objects.create_user(username="foreign", password="safe-test")
        other = create_workspace(owner=foreign_owner, name="Other")
        with self.assertRaises(WorkspaceAccessDenied):
            get_authorized_snapshot(user=self.member, workspace_id=other.pk, snapshot_id=uuid.uuid4())


class InitialWorkspaceProvisioningCommandTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="initial-admin", password="test-only-password")

    def run_command(self, **overrides):
        options = {"username": "initial-admin", "name": "Initial Team", "description": "Provisioned for testing"}
        options.update(overrides)
        output = StringIO()
        call_command("provision_initial_workspace", stdout=output, **options)
        return output.getvalue()

    def test_successful_provisioning_assigns_the_specified_superuser_as_owner(self):
        output = self.run_command()
        workspace = Workspace.objects.get(is_initial=True)
        membership = WorkspaceMembership.objects.get(workspace=workspace, user=self.admin)
        self.assertEqual(workspace.name, "Initial Team")
        self.assertEqual(workspace.description, "Provisioned for testing")
        self.assertEqual(membership.role, WorkspaceMembership.Role.OWNER)
        self.assertEqual(Workspace.objects.count(), 1)
        self.assertIn("Created initial workspace", output)

    def test_nonexistent_user_is_rejected_without_creating_data(self):
        with self.assertRaisesRegex(CommandError, "does not exist"):
            self.run_command(username="missing-admin")
        self.assertFalse(Workspace.objects.exists())

    def test_inactive_user_is_rejected_without_creating_data(self):
        self.admin.is_active = False
        self.admin.save(update_fields=("is_active",))
        with self.assertRaisesRegex(CommandError, "inactive"):
            self.run_command()
        self.assertFalse(Workspace.objects.exists())

    def test_non_superuser_is_rejected_without_creating_data(self):
        regular = User.objects.create_user(username="regular", password="test-only-password")
        with self.assertRaisesRegex(CommandError, "not a superuser"):
            self.run_command(username=regular.username)
        self.assertFalse(Workspace.objects.exists())

    def test_repeated_execution_is_idempotent(self):
        self.run_command()
        output = self.run_command()
        self.assertEqual(Workspace.objects.filter(is_initial=True).count(), 1)
        self.assertEqual(Workspace.objects.count(), 1)
        self.assertEqual(WorkspaceMembership.objects.filter(user=self.admin).count(), 1)
        self.assertIn("already provisioned", output)

    def test_existing_membership_without_initial_marker_is_rejected_as_ambiguous(self):
        existing = create_workspace(owner=self.admin, name="Existing Team")
        with self.assertRaisesRegex(CommandError, "already belongs to another workspace"):
            self.run_command()
        self.assertEqual(Workspace.objects.count(), 1)
        self.assertEqual(WorkspaceMembership.objects.get(workspace=existing, user=self.admin).role,
                         WorkspaceMembership.Role.OWNER)

    def test_existing_unmarked_workspace_is_rejected_as_ambiguous(self):
        Workspace.objects.create(name="Unmarked Team")
        with self.assertRaisesRegex(CommandError, "without an initial workspace marker"):
            self.run_command()
        self.assertEqual(Workspace.objects.count(), 1)

    def test_mismatched_repeat_does_not_silently_create_another_workspace(self):
        self.run_command()
        with self.assertRaisesRegex(CommandError, "does not match this request"):
            self.run_command(name="Different Team")
        self.assertEqual(Workspace.objects.count(), 1)

    def test_initial_workspace_database_constraint_rejects_a_second_marker(self):
        self.run_command()
        second_owner = User.objects.create_superuser(username="second-initial-admin", password="test-only-password")
        with self.assertRaises(ValidationError):
            create_workspace(owner=second_owner, name="Second Initial", is_initial=True)
        self.assertEqual(Workspace.objects.filter(is_initial=True).count(), 1)

    def test_membership_failure_rolls_back_workspace_creation(self):
        with patch("apps.repositories.services.WorkspaceMembership.objects.create",
                   side_effect=RuntimeError("injected membership failure")):
            with self.assertRaisesRegex(RuntimeError, "injected membership failure"):
                self.run_command()
        self.assertFalse(Workspace.objects.exists())
        self.assertFalse(WorkspaceMembership.objects.exists())


class RepositoryDomainTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="repo-user", password="safe-test")
        self.workspace = create_workspace(owner=self.user, name="Repository team")
        self.repository = Repository.objects.create(workspace=self.workspace, provider_repository_id="12345", owner="org", name="repo", canonical_url="https://github.com/org/repo", visibility="public")

    def _create_sealed_snapshot(self, **kwargs):
        kwargs.setdefault("content_digest", "e" * 64)
        snapshot = RepositorySnapshot(**kwargs)
        # These domain tests need a sealed-row fixture; lifecycle tests exercise
        # the actual verified storage service before sealing.
        snapshot._allow_snapshot_seal = True
        try:
            snapshot.save()
        finally:
            del snapshot._allow_snapshot_seal
        return snapshot

    def test_submission_is_workspace_scoped_and_idempotency_is_unique(self):
        fields = dict(workspace=self.workspace, submitted_by=self.user, submitted_url="https://github.com/org/repo", normalized_locator="org/repo", idempotency_key="key", request_fingerprint="a" * 64)
        RepositorySubmission.objects.create(**fields)
        with self.assertRaises(ValidationError):
            RepositorySubmission.objects.create(**fields)

    def test_model_full_clean_rejects_invalid_commit_sha(self):
        snapshot = RepositorySnapshot(workspace=self.workspace, repository=self.repository, commit_sha="bad", visibility_at_capture="public", manifest_schema_version="v1")
        with self.assertRaises(ValidationError):
            snapshot.full_clean()

    def test_workspace_repository_mismatches_are_rejected(self):
        foreign_user = User.objects.create_user(username="repo-foreign", password="x")
        foreign_workspace = create_workspace(owner=foreign_user, name="foreign")
        with self.assertRaises(ValidationError):
            RepositorySubmission(workspace=foreign_workspace, submitted_by=self.user, submitted_url="https://github.com/org/repo", normalized_locator="org/repo", idempotency_key="x", request_fingerprint="a" * 64, repository=self.repository).full_clean()
        grant = RepositoryGrant(workspace=foreign_workspace, repository=self.repository, permissions=["contents_read"], status="pending")
        with self.assertRaises(ValidationError):
            grant.full_clean()

    def test_private_snapshot_requires_current_verified_read_grant(self):
        grant = RepositoryGrant.objects.create(workspace=self.workspace, repository=self.repository, permissions=["contents_read"], status="verified_read", checked_at=datetime.now(timezone.utc))
        snapshot = self._create_sealed_snapshot(workspace=self.workspace, repository=self.repository, source_grant=grant, commit_sha="a" * 40, visibility_at_capture="private", status="sealed", manifest_schema_version="v1", manifest_digest="b" * 64, manifest_storage_ref="manifest", storage_ref="source", sealed_at=datetime.now(timezone.utc))
        self.assertEqual(get_authorized_snapshot(user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk), snapshot)
        for changes in ({"status": "pending"}, {"status": "revoked", "revoked_at": datetime.now(timezone.utc)}, {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}, {"checked_at": None}):
            RepositoryGrant.objects.filter(pk=grant.pk).update(**changes)
            with self.assertRaises(RepositoryGrantRequired):
                get_authorized_snapshot(user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk)

    def test_workspace_scoped_repository_lookup_hides_foreign_repository(self):
        foreign_user = User.objects.create_user(username="repo-foreign2", password="x")
        foreign_workspace = create_workspace(owner=foreign_user, name="foreign2")
        self.assertIsNone(get_repository(user=self.user, workspace_id=self.workspace.pk, repository_id=uuid.uuid4()))
        with self.assertRaises(WorkspaceAccessDenied):
            get_repository(user=self.user, workspace_id=foreign_workspace.pk, repository_id=self.repository.pk)

    def test_audit_events_reject_mutation_and_credential_like_data(self):
        event = create_audit_event(workspace_id=self.workspace.pk, actor=self.user, action="snapshot_read", outcome="allowed")
        with self.assertRaises(ValidationError):
            event.action = "changed"
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            create_audit_event(workspace_id=self.workspace.pk, actor=self.user, action="Bearer token=abc", outcome="denied")
        self.assertEqual(list_audit_events(user=self.user, workspace_id=self.workspace.pk).count(), 1)

    def test_execution_binding_is_snapshot_bound_and_immutable(self):
        snapshot = self._create_sealed_snapshot(workspace=self.workspace, repository=self.repository, commit_sha="c" * 40, visibility_at_capture="public", status="sealed", manifest_schema_version="v1", manifest_digest="d" * 64, manifest_storage_ref="manifest", storage_ref="source", sealed_at=datetime.now(timezone.utc))
        binding = create_execution_snapshot_binding(user=self.user, workspace_id=self.workspace.pk, execution_id=uuid.uuid4(), repository=self.repository, snapshot=snapshot, agent_ids=["structure_analyzer", "business_logic_analyst"], agent_group_ids=[])
        with self.assertRaises(ValidationError):
            binding.commit_sha = "e" * 40
            binding.save()
        with self.assertRaises(ValidationError):
            ExecutionSnapshotBinding.objects.filter(pk=binding.pk).update(commit_sha="e" * 40)

    def test_snapshot_access_requires_sealed_and_workspace_membership(self):
        snapshot = RepositorySnapshot.objects.create(workspace=self.workspace, repository=self.repository, commit_sha="a" * 40, visibility_at_capture="public", status="resolving", manifest_schema_version="repository.snapshot-manifest.v1")
        with self.assertRaises(SnapshotNotSealed):
            get_authorized_snapshot(user=self.user, workspace_id=self.workspace.pk, snapshot_id=snapshot.pk)


class RepositoryContractTests(TestCase):
    def test_repository_metadata_accepts_valid_full_sha(self):
        contract = RepositoryMetadata(identity=RepositoryIdentity(provider="github", provider_repository_id="1", owner="org", name="repo", canonical_url="https://github.com/org/repo"), visibility="public", resolved_ref="main", commit_sha="a" * 40, observed_at=datetime.now(timezone.utc))
        self.assertEqual(contract.commit_sha, "a" * 40)

    def test_contracts_reject_extra_fields_and_invalid_values(self):
        with self.assertRaises(PydanticValidationError):
            RepositoryAccessState(status="verified_read", unexpected="secret")
        with self.assertRaises(PydanticValidationError):
            SnapshotMetadata(snapshot_id=uuid.uuid4(), workspace_id=uuid.uuid4(), repository_id=uuid.uuid4(), commit_sha="short", visibility="private", status="sealed", manifest_schema_version="v1", created_at=datetime.now(timezone.utc))
        with self.assertRaises(PydanticValidationError):
            RepositoryOperationResult(operation_id=uuid.uuid4(), status="success", retryable=False, correlation_id="x")

