from pathlib import PurePosixPath


EXTENSIONS = {
    ".py": "Python", ".pyi": "Python", ".pyw": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".java": "Java",
    ".c": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".cs": "C#", ".go": "Go", ".rs": "Rust", ".php": "PHP", ".rb": "Ruby", ".swift": "Swift",
    ".kt": "Kotlin", ".kts": "Kotlin", ".dart": "Dart", ".html": "HTML", ".htm": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".sql": "SQL", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell",
    ".psm1": "PowerShell", ".r": "R", ".scala": "Scala", ".lua": "Lua", ".vue": "Vue", ".svelte": "Svelte",
}
SPECIAL_FILENAMES = {"makefile": ("Makefile", "filename"), "dockerfile": ("Dockerfile", "filename"),
                     "jenkinsfile": ("Groovy", "filename"), "gemfile": ("Ruby", "filename"),
                     "rakefile": ("Ruby", "filename"), "justfile": ("Just", "filename")}


def extension(path: str) -> str:
    name = PurePosixPath(path).name
    if name.startswith(".") and name.count(".") == 1:
        return "[dotfile]"
    return PurePosixPath(name).suffix.lower() or "[none]"


def classify_file(path: str):
    """Return (language, method, certainty); unknowns remain explicitly unknown."""
    name = PurePosixPath(path).name.casefold()
    if name in SPECIAL_FILENAMES:
        language, method = SPECIAL_FILENAMES[name]
        return language, method, "high"
    ext = PurePosixPath(path).suffix.lower()
    if ext in EXTENSIONS:
        return EXTENSIONS[ext], "extension", "high"
    return "Unknown", "none", "unknown"
