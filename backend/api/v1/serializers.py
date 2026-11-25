"""Strict request serializers for the authenticated workflow API."""
from rest_framework import serializers

from orchestration.contracts import GROUPS


class RepositorySubmissionSerializer(serializers.Serializer):
    repository_url = serializers.CharField(max_length=500)
    ref = serializers.CharField(max_length=255, required=False, default="")
    ref_kind = serializers.ChoiceField(choices=("branch", "tag", "commit"), default="branch")


class WorkflowStartSerializer(serializers.Serializer):
    submission_id = serializers.UUIDField()
    groups = serializers.ListField(child=serializers.ChoiceField(choices=GROUPS), required=False,
                                    default=list(GROUPS), allow_empty=True)
    approval_required = serializers.BooleanField(default=False)

    def validate_groups(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Agent groups must not contain duplicates.")
        return value


class ApprovalSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=("approved", "rejected"))
    proposal_ids = serializers.ListField(child=serializers.CharField(max_length=128),
                                         allow_empty=False, max_length=30)

    def validate_proposal_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Proposal IDs must not contain duplicates.")
        return value


class SessionLoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(max_length=1024, trim_whitespace=False, write_only=True)
