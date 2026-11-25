"""Non-executing repository metadata, manifest, route, schema, and module inventory."""

import ast
import json
import posixpath
import re
import tomllib


_LANGUAGES = {"py": "Python", "js": "JavaScript", "jsx": "JavaScript", "mjs": "JavaScript",
    "cjs": "JavaScript", "ts": "TypeScript", "tsx": "TypeScript", "java": "Java",
    "kt": "Kotlin", "go": "Go", "rs": "Rust", "cs": "C#", "php": "PHP",
    "rb": "Ruby", "swift": "Swift", "c": "C", "cc": "C++", "cpp": "C++", "h": "C/C++", "hpp": "C++"}
_TEXT_EXTENSIONS = set(_LANGUAGES) | {"txt", "toml", "ini", "cfg", "conf", "json", "yaml", "yml",
    "xml", "gradle", "sql", "proto", "graphql", "properties", "env", "lock"}
_MANIFEST_NAMES = {"pyproject.toml", "requirements.txt", "setup.cfg", "setup.py", "pipfile",
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts", "cargo.toml", "go.mod",
    "composer.json", "gemfile", "dockerfile", "makefile", "justfile"}
_VERSION_SPEC = re.compile(r"^(===|==|~=|!=|<=|>=|<|>)([A-Za-z0-9][A-Za-z0-9.*+!_-]*)$")
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^]]+\])?\s*([^;#\s]+)?")
_EXACT_VERSION = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")
_VERSION_SAFE = re.compile(r"^[A-Za-z0-9.*+!_(),<>=~ -]{1,150}$")


def extension(path):
    name = path.rsplit("/", 1)[-1].casefold()
    if name == ".env" or name.startswith(".env."):
        return "env"
    return name.rsplit(".", 1)[-1] if "." in name else ""


def is_text_candidate(path):
    name = path.rsplit("/", 1)[-1].casefold()
    return extension(path) in _TEXT_EXTENSIONS or name in _MANIFEST_NAMES or name.startswith("requirements")


def is_priority_file(path):
    name = path.rsplit("/", 1)[-1].casefold()
    return name in _MANIFEST_NAMES or name.startswith("requirements") or name in {
        "openapi.yaml", "openapi.yml", "swagger.json", "schema.sql", "schema.graphql"}


def inventory_project(files, all_paths):
    """Analyze already bounded file text. Returns facts tied to source paths and lines."""
    facts, dependencies, edges, assets = [], [], [], []
    module_for_path = {path: _module_name(path) for path in files if path.endswith(".py")}
    module_paths = {name: path for path, name in module_for_path.items() if name}

    ext_paths = {}
    for path in all_paths:
        ext = extension(path)
        language = _LANGUAGES.get(ext)
        if language:
            ext_paths.setdefault(language, path)
    for language, path in sorted(ext_paths.items()):
        facts.append(_fact("language", language, "source files present", path, None))

    for path, source in sorted(files.items()):
        name = path.rsplit("/", 1)[-1].casefold()
        ext = extension(path)
        if name in _MANIFEST_NAMES or name.startswith("requirements"):
            build = _build_system(name)
            if build:
                facts.append(_fact("build_system", build, "project configuration file present", path, 1))
        if name.startswith("requirements"):
            dependencies.extend(_requirements(path, source))
        elif name == "pyproject.toml":
            facts.extend(_pyproject_facts(path, source))
            dependencies.extend(_pyproject_dependencies(path, source))
        elif name == "package.json":
            dependencies.extend(_package_json_dependencies(path, source))
        elif name in {"pom.xml", "build.gradle", "build.gradle.kts", "cargo.toml", "go.mod", "gemfile", "composer.json"}:
            facts.append(_fact("build_system", _build_system(name) or name, "manifest present; dependency parsing is limited", path, 1))

        if _is_migration_asset(path):
            asset_type = _asset_type(path)
            assets.append({"path": path, "asset_type": asset_type, "line": 1 if source else None})
            facts.append(_fact("migration_asset", asset_type.replace("_", " "), "migration or schema artifact present", path, 1 if source else None))
        if _is_api_contract(path):
            assets.append({"path": path, "asset_type": "api_contract", "line": 1 if source else None})
            facts.append(_fact("api_contract", "API or interface contract", "contract artifact present", path, 1 if source else None))
        if _is_test_path(path):
            facts.append(_fact("test", "test file", "test-named path is present; execution and coverage were not measured", path, 1 if source else None))
        if name == ".env.example" or name.startswith(".env.example."):
            count = sum(1 for line in source.splitlines() if line.strip() and not line.lstrip().startswith("#") and "=" in line)
            facts.append(_fact("environment", "example environment template", f"{count} variable declarations; values omitted", path, None))

        if ext != "py":
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported = _import_targets(path, node)
                for target in imported:
                    if target in module_paths and module_paths[target] != path:
                        edges.append({"source_module": module_for_path[path], "target_module": target,
                                      "path": path, "line": node.lineno})
                if isinstance(node, ast.Import):
                    imported_names = [item.name for item in node.names]
                else:
                    imported_names = ["." * node.level + (node.module or "")]
                for imported_name in imported_names:
                    if imported_name in {"imp", "distutils", "pkg_resources"} or imported_name.startswith(("imp.", "distutils.", "pkg_resources.")):
                        facts.append(_fact("legacy_indicator", imported_name, "legacy compatibility-sensitive import; current support status was not checked", path, node.lineno))
                    if imported_name.startswith(("django.db", "sqlalchemy", "sqlite3", "psycopg", "pymysql")):
                        facts.append(_fact("database", imported_name.split(".")[0], "database-related import present; runtime configuration is unknown", path, node.lineno))
                    if imported_name.startswith(("pytest", "unittest")):
                        facts.append(_fact("test", imported_name.split(".")[0], "test framework import observed", path, node.lineno))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    route = _route_name(decorator)
                    if route:
                        facts.append(_fact("api_contract", route, "route decorator observed; route behavior was not executed", path, decorator.lineno))
            elif isinstance(node, ast.Call):
                call = _call_name(node.func)
                if call.split(".")[-1] in {"path", "re_path", "url", "route", "get", "post", "put", "delete", "patch"}:
                    if any(token in call.casefold() for token in ("path", "route", "url")):
                        facts.append(_fact("api_contract", call, "route registration call observed; route behavior was not executed", path, node.lineno))
                if call.endswith(("getenv", "environ.get")) or call == "os.environ.__getitem__":
                    facts.append(_fact("environment", "runtime environment lookup", "environment access observed; values were not read or retained", path, node.lineno))
            elif isinstance(node, ast.ClassDef) and any(_base_name(base).endswith((".Model", "Base")) for base in node.bases):
                facts.append(_fact("database", "model-like class", "class inherits a model-like base; schema semantics require review", path, node.lineno))

    for name in sorted({item["name"].casefold() for item in dependencies}):
        framework = _framework_name(name)
        if framework:
            dependency = next(item for item in dependencies if item["name"].casefold() == name)
            facts.append(_fact("framework", framework,
                f"declared dependency; version constraint: {dependency['version_spec'] or 'not specified'}",
                dependency["path"], dependency["line"]))

    return {"facts": _dedupe_facts(facts), "dependencies": _dedupe_dependencies(dependencies),
            "internal_dependencies": _dedupe_edges(edges)[:1500], "migration_assets": _dedupe_assets(assets),
            "dependencies_truncated": len(edges) > 1500}


def _fact(category, name, value, path, line):
    return {"category": category, "name": _safe_name(name), "value": _safe_value(value),
            "path": path, "line": line}


def _requirements(path, source):
    found = []
    for line_number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-", ".", "git+", "http:" ,"https:")):
            continue
        match = _REQ_LINE.match(line)
        if not match:
            continue
        name, spec = match.groups()
        name = name.split("[")[0]
        spec = spec.strip() if spec else None
        exact = None
        version_spec = None
        if spec:
            version = _VERSION_SPEC.match(spec)
            if version:
                version_spec = spec
                if version.group(1) in {"==", "==="} and "*" not in version.group(2):
                    exact = version.group(2)
            elif _EXACT_VERSION.fullmatch(spec):
                version_spec = "==" + spec
                exact = spec
        found.append({"ecosystem": "python", "name": name, "version_spec": version_spec,
                      "exact_version": exact, "path": path, "line": line_number})
    return found


def _pyproject_facts(path, source):
    try:
        document = tomllib.loads(source)
    except (tomllib.TOMLDecodeError, RecursionError):
        return []
    facts = []
    project = document.get("project", {})
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str) and requires_python.strip():
        facts.append(_fact("runtime", "Python requirement", requires_python[:300], path, 1))
    build = document.get("build-system", {})
    backend = build.get("build-backend")
    if isinstance(backend, str) and _PACKAGE_NAME.fullmatch(backend.replace(".", "-")):
        facts.append(_fact("build_system", backend, "declared PEP 517 build backend", path, 1))
    return facts


def _pyproject_dependencies(path, source):
    try:
        project = tomllib.loads(source).get("project", {})
    except (tomllib.TOMLDecodeError, RecursionError):
        return []
    entries = project.get("dependencies", [])
    if not isinstance(entries, list):
        return []
    found = []
    for entry in entries:
        if not isinstance(entry, str) or len(entry) > 300 or "@" in entry:
            continue
        match = _REQ_LINE.match(entry)
        if not match:
            continue
        name, spec = match.groups()
        version_spec, exact = _normalized_spec(spec)
        found.append({"ecosystem": "python", "name": name.split("[")[0],
            "version_spec": version_spec, "exact_version": exact, "path": path, "line": 1})
    return found


def _package_json_dependencies(path, source):
    try:
        document = json.loads(source)
    except (json.JSONDecodeError, RecursionError):
        return []
    found = []
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        entries = document.get(section, {})
        if not isinstance(entries, dict):
            continue
        for name, spec in entries.items():
            if not isinstance(name, str) or not _PACKAGE_NAME.fullmatch(name) or not isinstance(spec, str):
                continue
            version_spec, exact = _normalized_spec(spec)
            found.append({"ecosystem": "npm", "name": name, "version_spec": version_spec,
                "exact_version": exact, "path": path, "line": 1})
    return found


def _normalized_spec(spec):
    if not isinstance(spec, str) or not spec or not _VERSION_SAFE.fullmatch(spec):
        return None, None
    if _EXACT_VERSION.fullmatch(spec):
        return "==" + spec, spec
    if _VERSION_SPEC.fullmatch(spec):
        match = _VERSION_SPEC.fullmatch(spec)
        exact = match.group(2) if match.group(1) in {"==", "==="} and "*" not in match.group(2) else None
        return spec, exact
    return spec, None


def _build_system(name):
    return {"pyproject.toml": "PEP 517 / pyproject", "requirements.txt": "pip requirements",
        "setup.cfg": "setuptools configuration", "setup.py": "setuptools setup script",
        "pipfile": "Pipenv", "package.json": "npm-compatible package manifest",
        "pom.xml": "Maven", "build.gradle": "Gradle", "build.gradle.kts": "Gradle Kotlin DSL",
        "cargo.toml": "Cargo", "go.mod": "Go modules", "composer.json": "Composer",
        "gemfile": "Bundler", "dockerfile": "Docker", "makefile": "Make"}.get(name)


def _is_migration_asset(path):
    parts = path.casefold().split("/")
    name = parts[-1]
    return (any(part in {"migrations", "alembic", "versions", "db/migrate"} for part in parts[:-1]) or
            name in {"schema.sql", "schema.prisma", "changelog.xml", "liquibase.properties"})


def _asset_type(path):
    name = path.rsplit("/", 1)[-1].casefold()
    if name in {"schema.sql", "schema.prisma"}:
        return "database_schema"
    return "schema_migration"


def _is_api_contract(path):
    name = path.rsplit("/", 1)[-1].casefold()
    return name.startswith(("openapi", "swagger")) or name.endswith((".proto", ".graphql", ".gql"))


def _is_test_path(path):
    parts = path.casefold().split("/")
    name = parts[-1]
    return any(part in {"tests", "test", "spec", "specs"} for part in parts[:-1]) or name.startswith(("test_", "spec_")) or name.endswith(("_test.py", ".test.js", ".spec.ts"))


def _module_name(path):
    stem = path[:-3].replace("/", ".")
    if stem.endswith(".__init__"):
        stem = stem[:-9]
    return stem if all(part.isidentifier() for part in stem.split(".")) else None


def _import_targets(path, node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    package = module_for_path = _module_name(path)
    if not package:
        return []
    package_parts = package.split(".")[:-1]
    if node.level:
        package_parts = package_parts[:max(0, len(package_parts) - node.level + 1)]
    if node.module:
        base = ".".join(package_parts + node.module.split("."))
    else:
        base = ".".join(package_parts)
    targets = [base] if base else []
    for alias in node.names:
        if alias.name != "*":
            targets.append(".".join(part for part in (base, alias.name) if part))
    return [item for item in targets if item]


def _call_name(node):
    parts = []
    while isinstance(node, (ast.Attribute, ast.Call)):
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        else:
            node = node.func
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _route_name(node):
    name = _call_name(node)
    return name if any(item in name.casefold() for item in ("route", "path", "endpoint")) else ""


def _base_name(node):
    return _call_name(node)


def _framework_name(package):
    return {"django": "Django", "fastapi": "FastAPI", "flask": "Flask", "starlette": "Starlette",
        "sqlalchemy": "SQLAlchemy", "react": "React", "next": "Next.js", "nextjs": "Next.js",
        "express": "Express", "spring-boot": "Spring Boot", "rails": "Ruby on Rails"}.get(package)


def _safe_name(value):
    value = str(value)[:180]
    return value if re.fullmatch(r"[A-Za-z0-9_.:/-]+", value) else "observed repository component"


def _safe_value(value):
    value = str(value)[:500]
    return value if re.fullmatch(r"[A-Za-z0-9_.:/+*!=<>() ,@-]+", value) else "observed; exact value omitted"


def _dedupe_facts(facts):
    found = {}
    for item in facts:
        found[(item["category"], item["name"], item["value"], item["path"], item["line"])] = item
    return list(found.values())[:1000]


def _dedupe_dependencies(items):
    found = {}
    for item in items:
        found[(item["ecosystem"], item["name"].casefold(), item["version_spec"], item["path"], item["line"])] = item
    return list(found.values())[:2000]


def _dedupe_edges(items):
    found = {}
    for item in items:
        found[(item["source_module"], item["target_module"], item["path"], item["line"])] = item
    return list(found.values())


def _dedupe_assets(items):
    found = {}
    for item in items:
        found[(item["path"], item["asset_type"])] = item
    return list(found.values())[:1000]
