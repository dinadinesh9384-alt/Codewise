"""Evidence construction helpers that bind references to one immutable snapshot."""

import hashlib

from .contracts.evidence import EvidenceReference, EvidenceType
from .errors import EvidenceValidationError
from .validation import validate_evidence


class EvidenceFactory:
    def __init__(self, *, snapshot_id, manifest_digest, metadata_reader):
        self.snapshot_id, self.manifest_digest = snapshot_id, manifest_digest
        self.metadata_reader = metadata_reader

    def file(self, path, *, start_line=None, end_line=None, symbol=None, source_tool_id=None, direct=True):
        metadata = self.metadata_reader(path)
        ref = EvidenceReference(evidence_id=hashlib.sha256(f"{self.snapshot_id}:{path}:{start_line}:{end_line}".encode()).hexdigest()[:32],
            evidence_type=EvidenceType.SYMBOL if symbol else EvidenceType.FILE, snapshot_id=self.snapshot_id,
            manifest_digest=self.manifest_digest, path=path, start_line=start_line, end_line=end_line,
            symbol=symbol, content_hash=metadata["sha256"], source_tool_id=source_tool_id, direct=direct)
        return validate_evidence(ref, snapshot_id=self.snapshot_id, manifest_digest=self.manifest_digest,
                                 file_metadata=self.metadata_reader)

    def validate(self, reference):
        return validate_evidence(reference, snapshot_id=self.snapshot_id, manifest_digest=self.manifest_digest,
                                 file_metadata=self.metadata_reader)
