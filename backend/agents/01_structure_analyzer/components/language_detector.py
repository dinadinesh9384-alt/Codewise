from .file_classifier import classify_file


def detect_language(path: str, content: bytes | None = None):
    language, method, certainty = classify_file(path)
    if language in {"Dockerfile", "Makefile", "Just"}:
        return "Unknown", "recognized_configuration_filename", "unknown"
    if content and content.startswith(b"#!"):
        first = content.splitlines()[0][:300].decode("utf-8", "ignore").casefold()
        if "python" in first and language in ("Python", "Unknown"):
            return "Python", "extension+shebang" if language == "Python" else "shebang", "high"
        if any(name in first for name in ("/sh", "bash", "zsh")) and language in ("Shell", "Unknown"):
            return "Shell", "extension+shebang" if language == "Shell" else "shebang", "high"
        if "node" in first and language in ("JavaScript", "Unknown"):
            return "JavaScript", "extension+shebang" if language == "JavaScript" else "shebang", "high"
    return language, method, certainty
