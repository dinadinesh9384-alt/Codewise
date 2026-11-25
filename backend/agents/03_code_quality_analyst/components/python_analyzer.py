"""Non executing Python AST metrics and quality candidates."""

import ast
import hashlib


_DECISION_NODES = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.comprehension)
_NESTING_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)


def analyze_python(source: str, *, duplicate_block_statements=5):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return {"status": "malformed", "error": type(exc).__name__, "functions": [], "classes": [],
                "imports": [], "handlers": [], "open_calls": [], "duplicates": [], "references": {}}

    functions, classes, imports, handlers, open_calls = [], [], [], [], []
    references = {}
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent): parents[child] = parent

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            parameters = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            if args.vararg: parameters.append(args.vararg)
            if args.kwarg: parameters.append(args.kwarg)
            if parameters and parameters[0].arg in {"self", "cls"}: parameters = parameters[1:]
            missing_annotations = sum(arg.annotation is None for arg in parameters) + (node.returns is None)
            scoped_nodes = _scope_nodes(node.body)
            complexity = 1 + sum(1 for child in scoped_nodes if isinstance(child, _DECISION_NODES))
            complexity += sum(max(0, len(child.values) - 1) for child in scoped_nodes if isinstance(child, ast.BoolOp))
            max_nesting = _max_nesting(node.body)
            start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            end = getattr(node, "end_lineno", node.lineno)
            qualified = _qualified_name(node, parents)
            functions.append({"name": node.name, "qualified_name": qualified, "start_line": start,
                "end_line": end, "length": max(1, end - start + 1), "cyclomatic_complexity": complexity,
                "nesting_depth": max_nesting, "parameter_count": len(parameters),
                "missing_annotations": missing_annotations, "public": not node.name.startswith("_")})
            references.setdefault(node.name, 0)
        elif isinstance(node, ast.ClassDef):
            start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            end = getattr(node, "end_lineno", node.lineno)
            classes.append({"name": _qualified_name(node, parents), "start_line": start, "end_line": end,
                            "length": max(1, end - start + 1), "method_count": sum(
                                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) for child in node.body)})
        elif isinstance(node, ast.Import):
            imports.extend({"module": alias.name, "line": node.lineno,
                            "names": [alias.asname or alias.name.split(".")[0]]} for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append({"module": "." * node.level + (node.module or ""), "line": node.lineno,
                            "names": [alias.asname or alias.name for alias in node.names]})
        elif isinstance(node, ast.ExceptHandler):
            exception_type = ast.unparse(node.type) if node.type else "bare"
            broad = node.type is None or exception_type in {"Exception", "BaseException"}
            swallowed = not node.body or all(isinstance(item, ast.Pass) or
                isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and item.value.value is Ellipsis
                for item in node.body)
            handlers.append({"start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
                "exception_type": exception_type, "broad": broad, "swallowed": swallowed})
        elif isinstance(node, ast.Call) and _call_name(node.func).split(".")[-1] == "open":
            if not _inside_with(node, parents):
                open_calls.append({"start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
                                   "call": _call_name(node.func)})
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            references[node.id] = references.get(node.id, 0) + 1
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            references[node.attr] = references.get(node.attr, 0) + 1

    duplicates = _duplicate_blocks(tree, duplicate_block_statements)
    functions.sort(key=lambda x: (x["start_line"], x["qualified_name"]))
    classes.sort(key=lambda x: (x["start_line"], x["name"]))
    imports.sort(key=lambda x: (x["line"], x["module"]))
    handlers.sort(key=lambda x: (x["start_line"], x["exception_type"]))
    return {"status": "parsed", "error": None, "functions": functions, "classes": classes,
            "imports": imports, "handlers": handlers, "open_calls": open_calls, "duplicates": duplicates,
            "references": references}


def _qualified_name(node, parents):
    names = [node.name]
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(names))


def _call_name(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _inside_with(node, parents):
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.With, ast.AsyncWith)): return True
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)): return False
        current = parents.get(current)
    return False


def _scope_nodes(body):
    nodes = []
    stack = list(body)
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _max_nesting(body):
    maximum = 0
    def visit(statements, depth):
        nonlocal maximum
        maximum = max(maximum, depth)
        for statement in statements:
            children = []
            if isinstance(statement, ast.If): children = [statement.body, statement.orelse]
            elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
                children = [statement.body, getattr(statement, "orelse", [])]
            elif isinstance(statement, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                children = [statement.body, statement.orelse, statement.finalbody] + [handler.body for handler in statement.handlers]
            elif isinstance(statement, ast.Match): children = [case.body for case in statement.cases]
            current = depth + 1 if isinstance(statement, _NESTING_NODES) else depth
            for child_body in children:
                visit(child_body, current)
    visit(body, 0)
    return maximum


def _duplicate_blocks(tree, count):
    occurrences = {}
    def scan(statements):
        for start in range(0, max(0, len(statements) - count + 1)):
            block = statements[start:start + count]
            normalized = ast.dump(ast.Module(body=block, type_ignores=[]), annotate_fields=True, include_attributes=False)
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            locations = occurrences.setdefault(digest, {"statement_count": count, "locations": []})["locations"]
            locations.append({"start_line": block[0].lineno,
                              "end_line": getattr(block[-1], "end_lineno", block[-1].lineno)})
        for statement in statements: visit(statement)
    def visit(node):
        for _field, value in ast.iter_fields(node):
            if isinstance(value, list):
                if value and all(isinstance(item, ast.stmt) for item in value):
                    scan(value)
                else:
                    for item in value:
                        if isinstance(item, ast.AST): visit(item)
            elif isinstance(value, ast.AST):
                visit(value)
    scan(tree.body)
    result = []
    for digest, data in sorted(occurrences.items()):
        unique = {(loc["start_line"], loc["end_line"]): loc for loc in data["locations"]}
        result.append({"normalized_hash": digest, "statement_count": data["statement_count"],
                       "locations": [unique[key] for key in sorted(unique)]})
    return result
