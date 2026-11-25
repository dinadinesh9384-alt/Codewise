import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    """CodeWise user; retains Django's standard AbstractUser behavior."""


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    is_initial = models.BooleanField(default=False)
    private_source_retention_days = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(1), MaxValueValidator(3650)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")
        constraints = [models.UniqueConstraint(fields=("is_initial",), condition=Q(is_initial=True),
                                                name="uniq_initial_workspace")]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not 1 <= self.private_source_retention_days <= 3650:
            raise ValidationError({"private_source_retention_days": "Retention must be between 1 and 3650 days."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MembershipQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Use workspace membership domain services to change memberships.")

    def delete(self):
        raise ValidationError("Use workspace membership domain services to remove memberships.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Use workspace membership domain services to change memberships.")


class WorkspaceMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="workspace_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MembershipQuerySet.as_manager()

    objects = MembershipQuerySet.as_manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=("workspace", "user"), name="uniq_workspace_member")]
        indexes = [models.Index(fields=("user", "role"), name="ws_user_role_idx"), models.Index(fields=("workspace", "role"), name="ws_role_idx")]

    def clean(self):
        if self.role not in self.Role.values:
            raise ValidationError({"role": "Select a valid workspace role."})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            Workspace.objects.select_for_update().get(pk=self.workspace_id)
            if self.pk and not self._state.adding:
                previous = type(self).objects.select_for_update().get(pk=self.pk)
                if previous.workspace_id != self.workspace_id:
                    raise ValidationError("Membership workspace cannot be changed.")
                if previous.role == self.Role.OWNER and self.role != self.Role.OWNER:
                    owners = type(self).objects.filter(workspace_id=self.workspace_id, role=self.Role.OWNER).count()
                    if owners <= 1:
                        raise ValidationError("A workspace must retain at least one owner.")
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            Workspace.objects.select_for_update().get(pk=self.workspace_id)
            current = type(self).objects.select_for_update().get(pk=self.pk)
            if current.role == self.Role.OWNER and type(self).objects.filter(workspace_id=self.workspace_id, role=self.Role.OWNER).count() <= 1:
                raise ValueError("A workspace must retain at least one owner.")
            return super().delete(*args, **kwargs)


class Repository(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"
        INTERNAL = "internal", "Internal"
        UNKNOWN = "unknown", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="repositories")
    provider = models.CharField(max_length=32, default="github")
    provider_repository_id = models.CharField(max_length=128)
    owner = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    canonical_url = models.URLField(max_length=500)
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.UNKNOWN)
    default_branch = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("workspace", "provider", "provider_repository_id"), name="uniq_ws_provider_repo")]
        indexes = [models.Index(fields=("workspace", "provider", "owner", "name"), name="repo_locator_idx")]

    def clean(self):
        if self.visibility not in self.Visibility.values:
            raise ValidationError({"visibility": "Select a valid repository visibility."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RepositoryGrant(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED_READ = "verified_read", "Verified read"
        DENIED = "denied", "Denied"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="repository_grants")
    repository = models.ForeignKey(Repository, on_delete=models.PROTECT, related_name="grants")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="initiated_repository_grants")
    grant_type = models.CharField(max_length=32, blank=True)
    provider_installation_id = models.CharField(max_length=128, blank=True)
    provider_account_id = models.CharField(max_length=128, blank=True)
    credential_ref = models.CharField(max_length=512, blank=True)
    permissions = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    granted_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("workspace", "repository"), condition=Q(status__in=("pending", "verified_read")), name="uniq_active_ws_repo_grant"),
            models.CheckConstraint(condition=~Q(status="verified_read") | Q(checked_at__isnull=False), name="verified_grant_checked"),
        ]
        indexes = [models.Index(fields=("workspace", "status"), name="grant_ws_status_idx")]

    def clean(self):
        errors = {}
        if self.repository_id and self.workspace_id and self.repository.workspace_id != self.workspace_id:
            errors["repository"] = "Repository must belong to this workspace."
        if self.actor_id and self.workspace_id and not WorkspaceMembership.objects.filter(workspace_id=self.workspace_id, user_id=self.actor_id).exists():
            errors["actor"] = "Grant actor must be a workspace member."
        if self.status == self.Status.VERIFIED_READ:
            if not self.checked_at:
                errors["checked_at"] = "Verified grants require a verification timestamp."
            if "contents_read" not in (self.permissions or []):
                errors["permissions"] = "Verified read grants require contents_read permission."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RepositorySubmission(models.Model):
    class Status(models.TextChoices):
        RESOLVING = "resolving", "Resolving"
        AWAITING_AUTHORIZATION = "awaiting_authorization", "Awaiting authorization"
        RETRIEVING = "retrieving", "Retrieving"
        VALIDATING = "validating", "Validating"
        SEALED = "sealed", "Sealed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="repository_submissions")
    submitted_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="repository_submissions")
    submitted_url = models.URLField(max_length=500)
    normalized_locator = models.CharField(max_length=512)
    repository = models.ForeignKey(Repository, null=True, blank=True, on_delete=models.PROTECT, related_name="submissions")
    snapshot = models.ForeignKey("RepositorySnapshot", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="submissions")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.RESOLVING)
    error_code = models.CharField(max_length=64, blank=True)
    idempotency_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{64}$")])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("workspace", "idempotency_key"), name="uniq_ws_submission_idem")]
        indexes = [models.Index(fields=("workspace", "status", "created_at"), name="submission_ws_state_idx")]

    def clean(self):
        errors = {}
        if self.repository_id and self.workspace_id and self.repository.workspace_id != self.workspace_id:
            errors["repository"] = "Repository must belong to this workspace."
        if self.snapshot_id and (self.snapshot.workspace_id != self.workspace_id or
                                 self.repository_id != self.snapshot.repository_id):
            errors["snapshot"] = "Submission snapshot must match its workspace and repository."
        if self.submitted_by_id and self.workspace_id and not WorkspaceMembership.objects.filter(workspace_id=self.workspace_id, user_id=self.submitted_by_id).exists():
            errors["submitted_by"] = "Submitter must be a workspace member."
        if self.idempotency_key and len(self.idempotency_key) > 128:
            errors["idempotency_key"] = "Idempotency key is too long."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RepositorySnapshotQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Use repository snapshot lifecycle services to change snapshots.")

    def delete(self):
        raise ValidationError("Repository snapshots are soft-deleted through lifecycle services.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Use repository snapshot lifecycle services to change snapshots.")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("Repository snapshots must be created through lifecycle services.")


class RepositorySnapshot(models.Model):
    class Status(models.TextChoices):
        RESOLVING = "resolving", "Resolving"
        RETRIEVING = "retrieving", "Retrieving"
        VALIDATING = "validating", "Validating"
        SEALED = "sealed", "Sealed"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        DELETING = "deleting", "Deleting"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="repository_snapshots")
    repository = models.ForeignKey(Repository, on_delete=models.PROTECT, related_name="snapshots")
    source_grant = models.ForeignKey(RepositoryGrant, null=True, blank=True, on_delete=models.SET_NULL, related_name="snapshots")
    commit_sha = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{40,64}$")])
    visibility_at_capture = models.CharField(max_length=10, choices=Repository.Visibility.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RESOLVING)
    manifest_schema_version = models.CharField(max_length=64)
    manifest_digest = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{64}$")], blank=True)
    manifest_storage_ref = models.CharField(max_length=512, blank=True)
    content_digest = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{64}$")], blank=True)
    storage_ref = models.CharField(max_length=512, blank=True)
    file_count = models.PositiveIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(default=0)
    provenance = models.JSONField(default=dict, blank=True)
    ingestion_policy_version = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sealed_at = models.DateTimeField(null=True, blank=True)
    last_analysis_completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    failure_category = models.CharField(max_length=64, blank=True)

    objects = RepositorySnapshotQuerySet.as_manager()

    _IDENTITY_FIELDS = (
        "workspace_id", "repository_id", "source_grant_id", "commit_sha", "visibility_at_capture",
        "manifest_schema_version",
    )
    _SEALED_CONTENT_FIELDS = (
        "manifest_digest", "manifest_storage_ref", "content_digest", "storage_ref", "file_count",
        "total_bytes", "provenance", "ingestion_policy_version", "sealed_at",
    )
    _ALLOWED_STATUS_TRANSITIONS = {
        Status.RESOLVING: {Status.RETRIEVING, Status.FAILED},
        Status.RETRIEVING: {Status.VALIDATING, Status.FAILED},
        Status.VALIDATING: {Status.RETRIEVING, Status.SEALED, Status.FAILED},
        Status.FAILED: {Status.RETRIEVING, Status.VALIDATING},
        Status.SEALED: {Status.EXPIRED, Status.DELETING},
        Status.EXPIRED: {Status.DELETING},
        Status.DELETING: {Status.DELETING, Status.DELETED},
        Status.DELETED: {Status.DELETED},
    }

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~Q(status="sealed") | (Q(sealed_at__isnull=False) & ~Q(manifest_digest="") & ~Q(storage_ref="") & ~Q(manifest_storage_ref="")), name="sealed_snapshot_has_refs"),
        ]
        indexes = [models.Index(fields=("workspace", "status", "expires_at"), name="snapshot_retention_idx"), models.Index(fields=("repository", "commit_sha"), name="snapshot_commit_idx")]

    def clean(self):
        errors = {}
        if self.repository_id and self.workspace_id and self.repository.workspace_id != self.workspace_id:
            errors["repository"] = "Repository must belong to this workspace."
        if self.source_grant_id and self.workspace_id:
            grant = self.source_grant
            if grant.workspace_id != self.workspace_id or (self.repository_id and grant.repository_id != self.repository_id):
                errors["source_grant"] = "Source grant must match snapshot workspace and repository."
        if self.visibility_at_capture in (Repository.Visibility.PRIVATE, Repository.Visibility.INTERNAL) and not self.source_grant_id:
            errors["source_grant"] = "Private snapshots require a repository grant reference."
        if self.status == self.Status.SEALED and (not self.sealed_at or not self.manifest_digest or not self.content_digest or not self.manifest_storage_ref or not self.storage_ref):
            errors["status"] = "Sealed snapshots require timestamp, manifest/content digests, and storage references."
        if self.status in (self.Status.RESOLVING, self.Status.RETRIEVING, self.Status.VALIDATING, self.Status.FAILED):
            if any((self.manifest_digest, self.manifest_storage_ref, self.storage_ref, self.content_digest)) or self.file_count or self.total_bytes or self.sealed_at:
                errors["status"] = "Unsealed snapshots cannot expose stored content references or sealed metadata."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        allow_seal = bool(getattr(self, "_allow_snapshot_seal", False))
        allow_retention = bool(getattr(self, "_allow_snapshot_retention_update", False))
        if self._state.adding:
            if self.status == self.Status.SEALED and not allow_seal:
                raise ValidationError("Snapshots must be sealed through verified storage services.")
            return super().save(*args, **kwargs)

        previous = type(self).objects.get(pk=self.pk)
        changed_identity = [name for name in self._IDENTITY_FIELDS if getattr(previous, name) != getattr(self, name)]
        if changed_identity:
            raise ValidationError("Repository snapshot identity and commit are immutable.")
        if self.status != previous.status:
            allowed = self._ALLOWED_STATUS_TRANSITIONS.get(previous.status, set())
            if self.status not in allowed:
                raise ValidationError("Repository snapshot lifecycle transition is invalid.")
            if self.status == self.Status.SEALED and not allow_seal:
                raise ValidationError("Snapshots must be sealed through verified storage services.")
        if previous.status == self.Status.SEALED:
            changed_content = [name for name in self._SEALED_CONTENT_FIELDS if getattr(previous, name) != getattr(self, name)]
            if changed_content:
                raise ValidationError("Sealed snapshot content references and manifest metadata are immutable.")
            changed_retention = any(
                getattr(previous, name) != getattr(self, name)
                for name in ("last_analysis_completed_at", "expires_at")
            )
            if changed_retention and not allow_retention and self.status == previous.status:
                raise ValidationError("Snapshot retention changes must use the retention service.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Repository snapshot data is deleted through lifecycle services.")


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Audit events are immutable.")

    def delete(self):
        raise ValidationError("Audit events are immutable.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Audit events are immutable.")


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="repository_audit_events")
    action = models.CharField(max_length=64)
    repository_id_ref = models.CharField(max_length=128, blank=True)
    submission_id_ref = models.CharField(max_length=128, blank=True)
    snapshot_id_ref = models.CharField(max_length=128, blank=True)
    correlation_id = models.CharField(max_length=128, blank=True)
    outcome = models.CharField(max_length=32)
    reason_code = models.CharField(max_length=64, blank=True)
    provider_request_id = models.CharField(max_length=128, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [models.Index(fields=("workspace", "occurred_at"), name="audit_ws_time_idx")]

    def clean(self):
        for field_name in ("action", "outcome", "reason_code", "correlation_id", "repository_id_ref", "submission_id_ref", "snapshot_id_ref", "provider_request_id"):
            value = getattr(self, field_name) or ""
            if any(marker in value.lower() for marker in ("token=", "authorization:", "bearer ", "password=", "secret=")):
                raise ValidationError({field_name: "Audit metadata cannot contain credential-like values."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit events are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events cannot be deleted through the application model.")


class ExecutionSnapshotBindingQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Execution snapshot bindings are immutable.")

    def delete(self):
        raise ValidationError("Execution snapshot bindings are immutable.")


class ExecutionSnapshotBinding(models.Model):
    """Immutable binding consumed by future orchestration; no execution engine here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution_id = models.UUIDField(unique=True)
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT, related_name="execution_snapshot_bindings")
    repository = models.ForeignKey(Repository, on_delete=models.PROTECT, related_name="execution_bindings")
    snapshot = models.ForeignKey(RepositorySnapshot, on_delete=models.PROTECT, related_name="execution_bindings")
    commit_sha = models.CharField(max_length=64, validators=[RegexValidator(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")])
    agent_ids = models.JSONField(default=list, blank=True)
    agent_group_ids = models.JSONField(default=list, blank=True)
    contract_version = models.CharField(max_length=64, default="execution.snapshot-binding.v1")
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ExecutionSnapshotBindingQuerySet.as_manager()

    class Meta:
        constraints = [models.CheckConstraint(condition=~Q(commit_sha=""), name="binding_commit_nonempty")]
        indexes = [models.Index(fields=("workspace", "created_at"), name="binding_ws_created_idx")]

    def clean(self):
        errors = {}
        if self.snapshot_id:
            snapshot = self.snapshot
            if snapshot.status != RepositorySnapshot.Status.SEALED:
                errors["snapshot"] = "Only sealed snapshots can be bound to an execution."
            if snapshot.workspace_id != self.workspace_id or snapshot.repository_id != self.repository_id:
                errors["snapshot"] = "Workspace and repository must match the snapshot."
            if snapshot.commit_sha != self.commit_sha:
                errors["commit_sha"] = "Commit SHA must match the snapshot."
        if self.agent_ids and any(not isinstance(value, str) or not value for value in self.agent_ids):
            errors["agent_ids"] = "Agent IDs must be non-empty stable identifiers."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Execution snapshot bindings are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Execution snapshot bindings cannot be deleted through the application model.")


class WorkflowExecution(models.Model):
    """Durable API-visible execution metadata and validated workflow results."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        TIMED_OUT = "timed_out", "Timed out"

    _ALLOWED_TRANSITIONS = {
        Status.QUEUED: {Status.RUNNING, Status.FAILED, Status.CANCELLED},
        Status.RUNNING: {Status.PENDING_APPROVAL, Status.COMPLETED, Status.PARTIAL,
                         Status.FAILED, Status.CANCELLED, Status.TIMED_OUT},
        Status.PENDING_APPROVAL: {Status.COMPLETED, Status.PARTIAL, Status.FAILED},
        Status.COMPLETED: set(), Status.PARTIAL: set(), Status.FAILED: set(),
        Status.CANCELLED: set(), Status.TIMED_OUT: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT, related_name="workflow_executions")
    submission = models.ForeignKey(RepositorySubmission, on_delete=models.PROTECT, related_name="workflow_executions")
    binding = models.OneToOneField(ExecutionSnapshotBinding, on_delete=models.PROTECT, related_name="workflow_execution")
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="workflow_executions")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.QUEUED)
    selected_groups = models.JSONField(default=list, blank=True)
    selected_agents = models.JSONField(default=list, blank=True)
    result = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    idempotency_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{64}$")])
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("workspace", "idempotency_key"), name="uniq_ws_execution_idem"),
        ]
        indexes = [models.Index(fields=("workspace", "status", "created_at"), name="exec_ws_status_created_idx")]

    def clean(self):
        if self.binding_id and (self.binding.workspace_id != self.workspace_id or
                                self.binding.snapshot.workspace_id != self.workspace_id or
                                (self.submission_id and self.submission.repository_id != self.binding.repository_id)):
            raise ValidationError("Execution binding must belong to the submission workspace and repository.")
        if self.submission_id and self.submission.workspace_id != self.workspace_id:
            raise ValidationError({"submission": "Execution submission must belong to its workspace."})
        if self.requested_by_id and not WorkspaceMembership.objects.filter(
                workspace_id=self.workspace_id, user_id=self.requested_by_id).exists():
            raise ValidationError({"requested_by": "Requester must be a workspace member."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            immutable = ("workspace_id", "submission_id", "binding_id", "requested_by_id", "selected_groups",
                         "selected_agents", "idempotency_key", "request_fingerprint")
            if any(getattr(previous, name) != getattr(self, name) for name in immutable):
                raise ValidationError("Workflow execution identity and selection are immutable.")
            if previous.status != self.status and self.status not in self._ALLOWED_TRANSITIONS[previous.status]:
                raise ValidationError("Workflow execution lifecycle transition is invalid.")
        self.full_clean()
        return super().save(*args, **kwargs)


class ApprovalDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution = models.ForeignKey(WorkflowExecution, on_delete=models.PROTECT, related_name="approval_decisions")
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT, related_name="approval_decisions")
    proposal_id = models.CharField(max_length=128)
    snapshot_id = models.UUIDField()
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="approval_decisions")
    decision = models.CharField(max_length=8, choices=Decision.choices)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("execution", "proposal_id"), name="uniq_exec_proposal_decision")]
        indexes = [models.Index(fields=("workspace", "decided_at"), name="approval_ws_decided_idx")]

    def clean(self):
        if self.execution_id and (self.execution.workspace_id != self.workspace_id or
                                  self.execution.binding.snapshot_id != self.snapshot_id):
            raise ValidationError("Approval must match the execution workspace and snapshot.")
        if self.actor_id and not WorkspaceMembership.objects.filter(
                workspace_id=self.workspace_id, user_id=self.actor_id).exists():
            raise ValidationError({"actor": "Approver must be a workspace member."})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Approval decisions are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)


class ApprovalOutboxEvent(models.Model):
    """Durable human decision command, independent of checkpoint progress."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        RETRYABLE = "retryable", "Retryable"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    execution = models.ForeignKey(WorkflowExecution, on_delete=models.PROTECT, related_name="approval_outbox_events")
    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT, related_name="approval_outbox_events")
    snapshot_id = models.UUIDField()
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="approval_outbox_events")
    decision = models.CharField(max_length=8, choices=ApprovalDecision.Decision.choices)
    proposal_ids = models.JSONField()
    request_fingerprint = models.CharField(max_length=64, validators=[RegexValidator(r"^[0-9a-f]{64}$")])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("execution",), name="uniq_approval_outbox_execution"),
        ]
        indexes = [models.Index(fields=("status", "lease_until", "created_at"),
                                name="approval_outbox_claim_idx")]

    def clean(self):
        if self.execution_id and (self.execution.workspace_id != self.workspace_id or
                                  self.execution.binding.snapshot_id != self.snapshot_id):
            raise ValidationError("Approval command must match execution workspace and snapshot.")
        if self.actor_id and not WorkspaceMembership.objects.filter(
                workspace_id=self.workspace_id, user_id=self.actor_id,
                role__in=(WorkspaceMembership.Role.OWNER, WorkspaceMembership.Role.ADMIN)).exists():
            raise ValidationError("Approval command actor must be a workspace owner or admin.")
        if not isinstance(self.proposal_ids, list) or not self.proposal_ids or any(
                not isinstance(value, str) or not value for value in self.proposal_ids):
            raise ValidationError({"proposal_ids": "Approval command requires proposal IDs."})

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.full_clean()
        else:
            previous = type(self).objects.get(pk=self.pk)
            immutable = ("execution_id", "workspace_id", "snapshot_id", "actor_id", "decision",
                         "proposal_ids", "request_fingerprint")
            if any(getattr(previous, name) != getattr(self, name) for name in immutable):
                raise ValidationError("Approval outbox command identity is immutable.")
            allowed = {
                self.Status.PENDING: {self.Status.PROCESSING, self.Status.FAILED},
                self.Status.RETRYABLE: {self.Status.PROCESSING, self.Status.FAILED},
                self.Status.PROCESSING: {self.Status.PROCESSING, self.Status.RETRYABLE,
                                         self.Status.COMPLETED, self.Status.FAILED},
                self.Status.COMPLETED: set(), self.Status.FAILED: set(),
            }
            if previous.status != self.status and self.status not in allowed[previous.status]:
                raise ValidationError("Approval outbox lifecycle transition is invalid.")
        return super().save(*args, **kwargs)
