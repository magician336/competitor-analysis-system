import ast
from pathlib import Path

from csv_cleaner import clean_customer_rows, clean_order_rows


ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "csv_cleaner"


def test_customer_golden_output_is_unchanged():
    rows = [
        {"customer_id": " C-01 ", "email": " ADA@EXAMPLE.COM ", "country": " CN "},
        {"customer_id": "C-02", "email": None, "country": "US"},
    ]
    assert clean_customer_rows(rows) == [
        {"customer_id": "c-01", "email": "ada@example.com", "country": "cn"},
        {"customer_id": "c-02", "email": "", "country": "us"},
    ]


def test_order_golden_output_is_unchanged():
    rows = [
        {"order_id": " O-9 ", "customer_id": " C-01 ", "status": " PAID "},
        {"order_id": "O-10", "customer_id": None, "status": "Pending"},
    ]
    assert clean_order_rows(rows) == [
        {"order_id": "o-9", "customer_id": "c-01", "status": "paid"},
        {"order_id": "o-10", "customer_id": "", "status": "pending"},
    ]


def _module_trees() -> dict[str, ast.Module]:
    return {
        path.stem: ast.parse(path.read_text(encoding="utf-8"))
        for path in PACKAGE.glob("*.py")
        if path.name != "__init__.py"
    }


def test_duplicate_cleaning_function_is_replaced_by_typed_shared_helper():
    trees = _module_trees()
    shared_functions = [
        node
        for node in trees["shared"].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    typed_helpers = [
        node
        for node in shared_functions
        if node.returns is not None
        and all(arg.annotation is not None for arg in node.args.args)
    ]
    assert typed_helpers, "shared.py must expose a fully type-annotated helper"

    for module_name in ("customers", "orders"):
        tree = trees[module_name]
        imports_shared = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "shared"
            and node.level == 1
            for node in tree.body
        )
        assert imports_shared, f"{module_name}.py must reuse shared.py"

    function_bodies: list[str] = []
    for module_name in ("customers", "orders"):
        for node in trees[module_name].body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_"):
                function_bodies.append(ast.dump(ast.Module(body=node.body, type_ignores=[])))
    assert len(function_bodies) == len(set(function_bodies)), "duplicate helper remains"


def test_internal_import_graph_is_acyclic():
    trees = _module_trees()
    graph: dict[str, set[str]] = {name: set() for name in trees}
    for name, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in graph:
                graph[name].add(node.module)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        assert name not in visiting, f"circular import detected at {name}"
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for module_name in graph:
        visit(module_name)
