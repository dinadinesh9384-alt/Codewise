"""Pure in-memory validation of a unified diff against immutable source bytes."""

import ast
import hashlib
import re

from agent_foundation.context import validate_relative_path

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)?(?:\r?\n)?$")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|private[_-]?key)\b\s*[:=]\s*['\"]?[^\s'\";,}]+")
_CREDENTIAL_URL = re.compile(r"(?i)https?://[^/@:\s]+:[^/@\s]+@")
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_PROTECTED_PARTS = {".git", ".github", "authentication", "authorization", "permissions", "security",
                    "agent_foundation", "llm_gateway", "credentials", "config", "settings"}
_PROTECTED_NAMES = {"settings.py", "secrets.py", "credentials.py", "authorized_keys", "id_rsa", "id_ed25519"}


def contains_secret_material(source: str) -> bool:
    return bool(_SENSITIVE_ASSIGNMENT.search(source) or _CREDENTIAL_URL.search(source) or _PRIVATE_KEY.search(source))


def is_protected_path(path: str) -> bool:
    validate_relative_path(path)
    parts = path.casefold().split("/")
    name = parts[-1]
    if any(part in _PROTECTED_PARTS for part in parts):
        return True
    if name in _PROTECTED_NAMES or name == ".env" or name.startswith(".env."):
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore")):
        return True
    return False


def validate_unified_diff(*, path: str, diff: str, source: bytes):
    """Validate headers, hunks and exact old context; return proposed bytes and hashes."""
    validate_relative_path(path)
    if is_protected_path(path):
        raise ValueError("Patch path is protected by repository security policy.")
    if not isinstance(source, bytes):
        raise ValueError("Patch base must be immutable source bytes.")
    original_hash = hashlib.sha256(source).hexdigest()
    try:
        original = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Binary or non UTF-8 patch targets are unsupported.") from exc
    lines = diff.splitlines(keepends=True)
    if len(lines) < 3 or not lines[0].startswith("--- a/") or not lines[1].startswith("+++ b/"):
        raise ValueError("Patch must contain one same-path unified diff header.")
    old_path, new_path = lines[0][6:].strip(), lines[1][6:].strip()
    if old_path != path or new_path != path:
        raise ValueError("Patch headers must identify the same authorized repository path.")
    source_lines = original.splitlines(keepends=True)
    output, cursor, hunk_count, changed = [], 0, 0, False
    index = 2
    while index < len(lines):
        match = _HUNK.match(lines[index])
        if not match:
            raise ValueError("Patch contains unsupported or malformed unified-diff metadata.")
        old_start = int(match.group(1)); old_count = int(match.group(2) or 1)
        new_start = int(match.group(3)); new_count = int(match.group(4) or 1)
        target = old_start if old_count == 0 else old_start - 1
        if target < cursor or target > len(source_lines):
            raise ValueError("Patch hunks overlap or point outside the original file.")
        output.extend(source_lines[cursor:target]); cursor = target
        new_target = new_start if new_count == 0 else new_start - 1
        if new_target != len(output):
            raise ValueError("Patch hunk output line range is inconsistent or overlapping.")
        index += 1; consumed_old = consumed_new = 0
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if line.startswith("\\ No newline at end of file"):
                index += 1
                continue
            if not line or line[0] not in " +-":
                raise ValueError("Patch contains an invalid hunk line.")
            prefix, text = line[0], line[1:]
            if prefix in " -":
                if cursor >= len(source_lines) or source_lines[cursor] != text:
                    raise ValueError("Patch context does not match the immutable source snapshot.")
                cursor += 1; consumed_old += 1
            if prefix in " +":
                output.append(text); consumed_new += 1
            if prefix in "+-": changed = True
            index += 1
        if consumed_old != old_count or consumed_new != new_count:
            raise ValueError("Patch hunk line counts do not match its header.")
        hunk_count += 1
    if not hunk_count or not changed:
        raise ValueError("Patch must contain at least one valid change hunk.")
    output.extend(source_lines[cursor:])
    proposed = "".join(output).encode("utf-8")
    if path.casefold().endswith(".py"):
        try:
            ast.parse(proposed.decode("utf-8"), filename=path)
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise ValueError("Proposed Python content does not parse syntactically.") from exc
    return {"original_content_hash": original_hash, "proposed_content_hash": hashlib.sha256(proposed).hexdigest(),
            "proposed_bytes": proposed}
