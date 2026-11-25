from django.db import transaction
from django.utils import timezone

from .errors import LastWorkspaceOwnerError, RepositoryGrantRequired, SnapshotNotSealed, WorkspaceAccessDenied
from .models import AuditEvent, ExecutionSnapshotBinding, Repository, RepositoryGrant, RepositorySnapshot, RepositorySubmission, Workspace, WorkspaceMembership


def require_workspace_membership(*, user, workspace_id, roles=None):
    if not user or not user.is_authenticated:
        raise WorkspaceAccessDenied("Authenticated workspace membership is required.")
    membership = WorkspaceMembership.objects.filter(workspace_id=workspace_id, user=user).first()
    if membership is None or (roles and membership.role not in roles):
        raise WorkspaceAccessDenied("Workspace access is not permitted.")
    return membership


@transaction.atomic
def create_workspace(*, owner, name, retention_days=30, description="", is_initial=False):
    if not owner or not owner.is_authenticated:
        raise WorkspaceAccessDenied("An authenticated owner is required.")
    workspace = Workspace(name=name, description=description, is_initial=is_initial,
                           private_source_retention_days=retention_days)
    workspace.full_clean()
    workspace.save()
    WorkspaceMembership.objects.create(workspace=workspace, user=owner, role=WorkspaceMembership.Role.OWNER)
    return workspace


@transaction.atomic
def change_membership_role(*, actor, membership_id, role):
    membership = WorkspaceMembership.objects.select_for_update().select_related("workspace").get(pk=membership_id)
    require_workspace_membership(user=actor, workspace_id=membership.workspace_id, roles={"owner", "admin"})
    workspace = Workspace.objects.select_for_update().get(pk=membership.workspace_id)
    membership = WorkspaceMembership.objects.select_for_update().get(pk=membership_id)
    if membership.role == WorkspaceMembership.Role.OWNER and role != WorkspaceMembership.Role.OWNER:
        if not WorkspaceMembership.objects.filter(workspace=workspace, role=WorkspaceMembership.Role.OWNER).exclude(pk=membership.pk).exists():
            raise LastWorkspaceOwnerError("A workspace must retain at least one owner.")
    membership.role = role
    membership.full_clean()
    membership.save(update_fields=("role", "updated_at"))
    return membership


@transaction.atomic
def remove_membership(*, actor, membership_id):
    membership = WorkspaceMembership.objects.select_for_update().select_related("workspace").get(pk=membership_id)
    require_workspace_membership(user=actor, workspace_id=membership.workspace_id, roles={"owner", "admin"})
    Workspace.objects.select_for_update().get(pk=membership.workspace_id)
    membership = WorkspaceMembership.objects.select_for_update().get(pk=membership_id)
    if membership.role == WorkspaceMembership.Role.OWNER and not WorkspaceMembership.objects.filter(workspace=membership.workspace, role="owner").exclude(pk=membership.pk).exists():
        raise LastWorkspaceOwnerError("A workspace must retain at least one owner.")
    membership.delete()


@transaction.atomic
def add_membership(*, actor, workspace_id, user, role="member"):
    require_workspace_membership(user=actor, workspace_id=workspace_id, roles={"owner", "admin"})
    Workspace.objects.select_for_update().get(pk=workspace_id)
    membership = WorkspaceMembership(workspace_id=workspace_id, user=user, role=role)
    membership.full_clean()
    membership.save()
    return membership


def create_repository(*, user, workspace_id, **values):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    repository = Repository(workspace_id=workspace_id, **values)
    repository.full_clean()
    repository.save()
    return repository


def create_submission(*, user, workspace_id, **values):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    submission = RepositorySubmission(workspace_id=workspace_id, submitted_by=user, **values)
    submission.full_clean()
    submission.save()
    return submission


def create_repository_grant(*, actor, workspace_id, **values):
    require_workspace_membership(user=actor, workspace_id=workspace_id, roles={"owner", "admin"})
    grant = RepositoryGrant(workspace_id=workspace_id, actor=actor, **values)
    grant.full_clean()
    grant.save()
    return grant


def create_snapshot_metadata(*, user, workspace_id, **values):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    snapshot = RepositorySnapshot(workspace_id=workspace_id, **values)
    snapshot.full_clean()
    snapshot.save()
    return snapshot


def create_execution_snapshot_binding(*, user, workspace_id, execution_id, repository, snapshot, agent_ids, agent_group_ids):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    binding = ExecutionSnapshotBinding(workspace_id=workspace_id, execution_id=execution_id,
        repository=repository, snapshot=snapshot, commit_sha=snapshot.commit_sha,
        agent_ids=agent_ids, agent_group_ids=agent_group_ids)
    binding.full_clean()
    binding.save()
    return binding


def create_audit_event(*, workspace_id, actor, action, outcome, reason_code="", correlation_id="", repository_id_ref="", submission_id_ref="", snapshot_id_ref="", provider_request_id=""):
    # Deliberately allow only short, structured identifiers; no free-form payload field exists.
    event = AuditEvent(workspace_id=workspace_id, actor=actor, action=action, outcome=outcome,
        reason_code=reason_code, correlation_id=correlation_id, repository_id_ref=repository_id_ref,
        submission_id_ref=submission_id_ref, snapshot_id_ref=snapshot_id_ref,
        provider_request_id=provider_request_id)
    event.full_clean()
    event.save()
    return event


def get_authorized_snapshot(*, user, workspace_id, snapshot_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    snapshot = RepositorySnapshot.objects.select_related("repository", "source_grant").filter(pk=snapshot_id, workspace_id=workspace_id).first()
    if snapshot is None:
        raise WorkspaceAccessDenied("Snapshot is not available in this workspace.")
    if snapshot.status != RepositorySnapshot.Status.SEALED:
        raise SnapshotNotSealed("Only sealed snapshots may be read for analysis.")
    if snapshot.expires_at is not None and snapshot.expires_at <= timezone.now():
        raise SnapshotNotSealed("Snapshot retention has expired.")
    if snapshot.repository.workspace_id != workspace_id:
        raise WorkspaceAccessDenied("Snapshot is not available in this workspace.")
    if snapshot.visibility_at_capture != "public":
        grant = snapshot.source_grant
        if (grant is None or grant.workspace_id != workspace_id or grant.repository_id != snapshot.repository_id
                or grant.status != RepositoryGrant.Status.VERIFIED_READ or not grant.checked_at
                or (grant.expires_at is not None and grant.expires_at <= timezone.now())
                or grant.revoked_at is not None or "contents_read" not in (grant.permissions or [])):
            raise RepositoryGrantRequired("Verified workspace repository read access is required.")
    return snapshot


def list_submissions(*, user, workspace_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    return RepositorySubmission.objects.filter(workspace_id=workspace_id).select_related("repository", "submitted_by")


def get_submission(*, user, workspace_id, submission_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    return RepositorySubmission.objects.filter(workspace_id=workspace_id, pk=submission_id).select_related("repository").first()


def get_repository(*, user, workspace_id, repository_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    return Repository.objects.filter(workspace_id=workspace_id, pk=repository_id).first()


def list_repositories(*, user, workspace_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    return Repository.objects.filter(workspace_id=workspace_id).order_by("owner", "name", "id")


def list_repository_grants(*, user, workspace_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    return RepositoryGrant.objects.filter(workspace_id=workspace_id).select_related("repository", "actor")


def list_snapshots(*, user, workspace_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    return RepositorySnapshot.objects.filter(workspace_id=workspace_id).select_related("repository")


def list_audit_events(*, user, workspace_id):
    require_workspace_membership(user=user, workspace_id=workspace_id)
    return AuditEvent.objects.filter(workspace_id=workspace_id).select_related("actor")
