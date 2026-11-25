from pathlib import PurePosixPath
from agent_foundation.context import validate_relative_path

from ..schemas.repository import EntryPoint

CONVENTIONS = {
    "manage.py": ("Python", "conventional_candidate", "Conventional Django management entry point."),
    "wsgi.py": ("Python", "conventional_candidate", "Conventional WSGI server module."),
    "asgi.py": ("Python", "conventional_candidate", "Conventional ASGI server module."),
    "app.py": ("Python", "weak_candidate", "Common Python application module name."),
    "main.py": ("Python", "weak_candidate", "Common Python application module name."),
    "server.py": ("Python", "weak_candidate", "Common Python server module name."),
    "index.js": ("JavaScript", "conventional_candidate", "Common JavaScript package entry file."),
    "index.jsx": ("JavaScript", "conventional_candidate", "Common React/JavaScript package entry file."),
    "index.ts": ("TypeScript", "conventional_candidate", "Common TypeScript package entry file."),
    "index.tsx": ("TypeScript", "conventional_candidate", "Common React/TypeScript package entry file."),
    "main.ts": ("TypeScript", "conventional_candidate", "Common TypeScript application entry file."),
    "main.js": ("JavaScript", "conventional_candidate", "Common JavaScript application entry file."),
    "artisan": ("PHP", "conventional_candidate", "Laravel command-line entry file."),
    "config.ru": ("Ruby", "conventional_candidate", "Rack application configuration entry file."),
    "program.cs": ("C#", "conventional_candidate", "Common .NET application entry file."),
    "main.java": ("Java", "weak_candidate", "Common Java class name; build configuration was not verified."),
    "main.go": ("Go", "conventional_candidate", "Common Go application entry file."),
    "main.rs": ("Rust", "conventional_candidate", "Common Rust application entry file."),
}


def detect_entry_points(paths, contents, evidence_for, *, enabled=True):
    if not enabled: return ()
    pathset = set(paths)
    package_data = contents.get("package.json") or next((v for p,v in contents.items() if p.endswith("/package.json")), None)
    configured_main = None
    if package_data:
        import json
        try:
            data = json.loads(package_data)
            configured_main = data.get("main") if isinstance(data, dict) else None
            if isinstance(configured_main, str):
                try: configured_main = validate_relative_path(configured_main.removeprefix("./"))
                except ValueError: configured_main = None
            if configured_main and configured_main in pathset:
                manifest_path = next(p for p in contents if p.endswith("package.json"))
                line_number = next((index for index, line in enumerate(contents[manifest_path].decode("utf-8", "replace").splitlines(), 1)
                                    if configured_main in line), None)
                return (EntryPoint(path=configured_main, ecosystem="javascript", classification="confirmed_configuration",
                    explanation="package.json declares this existing file as its main entry.", evidence=(
                        evidence_for(manifest_path, "entry_point_detector", line_number, line_number) if line_number
                        else evidence_for(manifest_path, "entry_point_detector"),
                        evidence_for(configured_main, "entry_point_detector"))),)
        except (ValueError, UnicodeDecodeError):
            pass
    result = []
    for path in sorted(pathset):
        name = PurePosixPath(path).name.casefold()
        candidate = CONVENTIONS.get(name)
        if not candidate and path in {"src/main.ts", "src/index.ts", "src/main.js", "src/index.js", "src/index.tsx",
                                      "src/main.tsx", "src/index.jsx", "src/main.jsx"}:
            candidate = ("JavaScript/TypeScript", "conventional_candidate", "Conventional source-directory entry file.")
        if candidate:
            ecosystem, classification, explanation = candidate
            result.append(EntryPoint(path=path, ecosystem=ecosystem, classification=classification,
                explanation=explanation, evidence=(evidence_for(path, "entry_point_detector"),)))
    return tuple(result)
