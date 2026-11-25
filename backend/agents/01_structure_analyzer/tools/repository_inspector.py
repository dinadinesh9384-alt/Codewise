"""Small repository-read facade; it has no storage or filesystem access."""

from agent_foundation.context import validate_relative_path
from agent_foundation.permissions import Permission, require_permissions
import re


class RepositoryInspector:
    def __init__(self, reader, context):
        self._reader, self._context = reader, context

    def inventory(self):
        require_permissions(self._context.permissions, {Permission.REPOSITORY_READ})
        if hasattr(self._reader, "list_file_metadata"):
            records = self._reader.list_file_metadata(self._context)
        else:
            records = tuple(self._reader.file_metadata(self._context, path)
                            for path in self._reader.list_paths(self._context))
        normalized = []
        for item in records:
            path = validate_relative_path(item["path"])
            size = item.get("size_bytes")
            digest = item.get("sha256")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError("snapshot returned invalid file size metadata")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("snapshot returned invalid file digest metadata")
            normalized.append({"path": path, "size_bytes": size, "sha256": digest})
        return tuple(sorted(normalized, key=lambda record: record["path"]))

    def read(self, path, *, max_bytes):
        require_permissions(self._context.permissions, {Permission.REPOSITORY_READ})
        validate_relative_path(path)
        return self._reader.read_file(self._context, path, max_bytes=max_bytes)
