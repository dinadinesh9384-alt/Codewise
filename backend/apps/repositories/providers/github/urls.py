"""Strict GitHub repository URL and commit identifier validation."""

import re
from urllib.parse import quote, urlsplit

from .contracts import GitHubRepositoryLocator
from .errors import InvalidGitHubURL

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def parse_repository_url(url: str) -> GitHubRepositoryLocator:
    """Parse only canonical HTTPS github.com owner/repository URLs."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise InvalidGitHubURL("Expected an HTTPS GitHub repository URL.")
    if (any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() or ch == "\\" for ch in url)
            or "?" in url or "#" in url):
        raise InvalidGitHubURL("Repository URL contains unsupported characters.")
    try:
        parsed = urlsplit(url)
        # Accessing .port also rejects malformed port syntax.
        port = parsed.port
    except ValueError as exc:
        raise InvalidGitHubURL("Repository URL is malformed.") from exc
    if (parsed.scheme.lower() != "https" or parsed.netloc.lower() != "github.com"
            or parsed.username is not None or parsed.password is not None or port is not None
            or parsed.query or parsed.fragment):
        raise InvalidGitHubURL("Only HTTPS github.com repository URLs are supported.")

    path = parsed.path
    if "%" in path:
        raise InvalidGitHubURL("Percent-encoded repository paths are not supported.")
    path = path[:-1] if path.endswith("/") else path
    parts = path.lstrip("/").split("/")
    if path.startswith("//") or len(parts) != 2 or not all(parts):
        raise InvalidGitHubURL("Expected exactly an owner and repository path.")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if (not _OWNER_RE.fullmatch(owner) or not _REPOSITORY_RE.fullmatch(name)
            or name in {".", ".."}):
        raise InvalidGitHubURL("GitHub owner or repository identifier is invalid.")
    return GitHubRepositoryLocator(owner=owner, name=name)


def validate_full_commit_sha(value: str) -> str:
    """Return a normalized full GitHub SHA-1, rejecting abbreviations."""
    if not isinstance(value, str) or not _FULL_SHA_RE.fullmatch(value):
        raise InvalidGitHubURL("A full 40-character hexadecimal commit SHA is required.")
    return value.lower()


def encode_ref_path(ref: str) -> str:
    """Validate and URL-encode a branch or tag without changing slash nesting."""
    if not isinstance(ref, str) or not ref:
        raise InvalidGitHubURL("Git reference must contain 1 to 255 UTF-8 bytes.")
    try:
        if len(ref.encode("utf-8")) > 255:
            raise InvalidGitHubURL("Git reference must contain 1 to 255 UTF-8 bytes.")
    except UnicodeEncodeError as exc:
        raise InvalidGitHubURL("Git reference contains invalid Unicode.") from exc
    if (ref.startswith("/") or ref.endswith("/") or "//" in ref or ".." in ref
            or "@{" in ref or ref == "@" or any(
                ord(ch) < 0x20 or ord(ch) == 0x7F or ch in " ~^:?*[\\" for ch in ref
            )):
        raise InvalidGitHubURL("Git reference contains unsupported characters.")
    components = ref.split("/")
    if any(not part or part.startswith(".") or part.endswith(".lock") for part in components):
        raise InvalidGitHubURL("Git reference contains an invalid path component.")
    return quote(ref, safe="/")
