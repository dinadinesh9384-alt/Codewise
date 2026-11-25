"""Input, output, and repository-bound evidence validation."""

from pydantic import BaseModel, ValidationError

from .context import validate_relative_path
from .errors import EvidenceValidationError, InvalidInputError, InvalidOutputError


def _safe_errors(exc):
    # Field locations are useful; Pydantic's input/context can contain source or secrets.
    return [{"loc": [str(part) for part in error["loc"]], "type": error["type"]} for error in exc.errors()]


def validate_input(model: type[BaseModel], value):
    try:
        return model.model_validate(value)
    except (ValidationError, TypeError, ValueError) as exc:
        details = _safe_errors(exc) if isinstance(exc, ValidationError) else {}
        raise InvalidInputError("Agent input does not match its registered contract.", details={"errors": details}) from exc


def validate_output(model: type[BaseModel], value):
    try:
        parsed = model.model_validate(value)
    except (ValidationError, TypeError, ValueError) as exc:
        details = _safe_errors(exc) if isinstance(exc, ValidationError) else {}
        raise InvalidOutputError("Agent output does not match its registered contract.", details={"errors": details}) from exc
    return parsed


def validate_evidence(reference, *, snapshot_id, manifest_digest, file_metadata):
    if reference.snapshot_id != snapshot_id or reference.manifest_digest != manifest_digest:
        raise EvidenceValidationError("Evidence refers to a different execution snapshot.")
    if reference.path:
        try:
            validate_relative_path(reference.path)
        except ValueError as exc:
            raise EvidenceValidationError("Evidence path is outside the repository.") from exc
        record = file_metadata(reference.path)
        if reference.content_hash and reference.content_hash != record["sha256"]:
            raise EvidenceValidationError("Evidence content hash does not match the sealed snapshot.")
        if reference.end_line is not None and reference.end_line > record.get("line_count", reference.end_line):
            raise EvidenceValidationError("Evidence line range exceeds the file.")
    elif reference.evidence_type.value != "inference" and reference.evidence_type.value != "tool_output":
        raise EvidenceValidationError("Evidence requires a repository path.")
    return reference
