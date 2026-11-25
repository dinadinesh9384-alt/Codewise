"""Static test-source inventory. This module never imports or executes inspected code."""

import ast
import json
import tomllib
from xml.etree import ElementTree


_EXT_LANGUAGE = {"py": "Python", "js": "JavaScript", "jsx": "JavaScript", "ts": "TypeScript",
    "tsx": "TypeScript", "java": "Java", "go": "Go", "rb": "Ruby", "php": "PHP"}
_TEST_DIRS = {"test", "tests", "spec", "specs"}
_TEST_NAMES = {"conftest.py", "pytest.ini", "tox.ini", ".coveragerc", "jest.config.js", "jest.config.ts",
    "vitest.config.js", "vitest.config.ts", "playwright.config.ts", "cypress.config.js", "package.json",
    "pyproject.toml", "setup.cfg", "requirements.txt", "coverage.json", "coverage.xml"}
_BOUNDARY_TERMS = ("empty", "none", "null", "zero", "limit", "max_", "min_", "boundary", "overflow", "timeout")


def extension(path):
    leaf = path.rsplit("/", 1)[-1]
    return leaf.rsplit(".", 1)[-1].casefold() if "." in leaf else ""


def is_test_file(path):
    parts = path.casefold().split("/")
    leaf = parts[-1]
    return (bool(set(parts[:-1]) & _TEST_DIRS) or leaf.startswith(("test_", "spec_")) or
            leaf.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts", ".spec.tsx")))


def is_test_config(path):
    leaf = path.rsplit("/", 1)[-1].casefold()
    return (leaf in _TEST_NAMES or leaf.startswith(("jest.config.", "vitest.config.", "playwright.config.", "cypress.config."))
            or leaf.startswith("requirements-test"))


def analyze_python_test_source(source):
    """Count syntax-visible tests, fixtures, assertions and mock idioms only."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {"status": "unparsed", "test_names": (), "test_case_count": 0, "fixture_count": 0,
                "assertion_count": 0, "mock_indicators": (), "boundary_indicators": ()}
    test_names, fixtures, assertions, mocks = [], 0, 0, set()
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.add(module)
            imports.update(f"{module}.{alias.name}" for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                test_names.append(node.name)
            if any(_decorator_name(decorator).endswith("fixture") for decorator in node.decorator_list):
                fixtures += 1
        elif isinstance(node, ast.Assert):
            assertions += 1
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name.split(".")[-1].startswith("assert") or name.endswith(("raises", "expect")):
                assertions += 1
            if any(term.casefold() in name.casefold() for term in ("mock", "patch", "monkeypatch")):
                mocks.add(name[:100])
    class_tests = 0
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {_call_name(base).split(".")[-1] for base in node.bases}
        if node.name.startswith("Test") or bases & {"TestCase", "IsolatedAsyncioTestCase"}:
            class_tests += sum(isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                member.name.startswith("test_") for member in node.body)
    discovered = sum(name.startswith("test_") for name in test_names)
    # Class methods are included in the AST name count already; take the maximum to avoid double counting.
    test_count = max(discovered, class_tests)
    for module in imports:
        if module in {"unittest.mock", "mock"} or module.startswith("pytest_mock"):
            mocks.add(module)
    lower = source.casefold()
    boundary = tuple(term for term in _BOUNDARY_TERMS if term in lower)
    frameworks = tuple(name for name, present in (("pytest", "pytest" in imports),
        ("unittest", any(item == "unittest" or item.startswith("unittest.") for item in imports))) if present)
    return {"status": "parsed", "frameworks": frameworks, "test_names": tuple(test_names[:1000]),
        "test_case_count": test_count, "fixture_count": fixtures, "assertion_count": assertions,
        "mock_indicators": tuple(sorted(mocks)[:20]), "boundary_indicators": boundary}


def frameworks_from_manifest(path, source):
    leaf = path.rsplit("/", 1)[-1].casefold()
    found = set()
    if leaf == "pyproject.toml":
        try:
            doc = tomllib.loads(source)
        except (tomllib.TOMLDecodeError, RecursionError):
            return ()
        text = json.dumps(doc, ensure_ascii=False).casefold()
        if "pytest" in text: found.add("pytest")
        if "unittest" in text: found.add("unittest")
    elif leaf.startswith("requirements") or leaf in {"setup.cfg", "package.json"}:
        text = source.casefold()
        for framework in ("pytest", "unittest", "jest", "vitest", "mocha", "playwright", "cypress"):
            if framework in text: found.add(framework)
        if leaf == "package.json":
            try:
                doc = json.loads(source)
            except (json.JSONDecodeError, RecursionError):
                doc = {}
            sections = (doc.get("devDependencies", {}), doc.get("dependencies", {})) if isinstance(doc, dict) else ()
            for section in sections:
                if isinstance(section, dict):
                    found.update(name for name in section if name.casefold() in {
                        "jest", "vitest", "mocha", "playwright", "cypress"})
    return tuple(sorted(found))


def parse_coverage_report(path, source):
    """Return only totals carried by a parseable, existing coverage report."""
    leaf = path.rsplit("/", 1)[-1].casefold()
    try:
        if leaf == "coverage.json":
            data = json.loads(source)
            totals = data.get("totals", {}) if isinstance(data, dict) else {}
            covered, valid = totals.get("covered_lines"), totals.get("num_statements")
        elif leaf == "coverage.xml":
            root = ElementTree.fromstring(source)
            valid = int(root.attrib.get("lines-valid", "0"))
            covered = int(root.attrib.get("lines-covered", "0"))
        else:
            return None
        if (not isinstance(covered, int) or isinstance(covered, bool) or
                not isinstance(valid, int) or isinstance(valid, bool) or valid <= 0 or covered < 0 or covered > valid):
            return None
        return {"covered_lines": covered, "valid_lines": valid, "percent_covered": round(covered * 100 / valid, 2)}
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, ElementTree.ParseError, RecursionError):
        return None


def _decorator_name(node):
    if isinstance(node, ast.Call): return _call_name(node.func)
    return _call_name(node)


def _call_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr); node = node.value
    if isinstance(node, ast.Name): parts.append(node.id)
    return ".".join(reversed(parts))
