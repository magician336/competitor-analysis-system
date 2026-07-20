import ast
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parent
CLIENT = TestClient(app)


def test_existing_list_endpoint_does_not_regress():
    response = CLIENT.get("/books")
    assert response.status_code == 200
    payload = response.json()
    assert [book["id"] for book in payload] == [1, 2, 3]
    assert all(set(book) == {"id", "title", "author"} for book in payload)


def test_existing_detail_endpoint_and_not_found_contract():
    response = CLIENT.get("/books/2")
    assert response.status_code == 200
    assert response.json() == {
        "id": 2,
        "title": "Domain-Driven Design",
        "author": "Eric Evans",
    }
    assert CLIENT.get("/books/999").status_code == 404


def test_title_search_contract_is_case_insensitive_and_stable():
    response = CLIENT.get("/books/search", params={"title": "pYtHoN"})
    assert response.status_code == 200
    assert response.json() == [
        {"id": 1, "title": "Python Engineering", "author": "Jane Doe"},
        {"id": 3, "title": "Fluent Python", "author": "Luciano Ramalho"},
    ]


def test_title_search_can_return_an_empty_list():
    response = CLIENT.get("/books/search", params={"title": "rust"})
    assert response.status_code == 200
    assert response.json() == []


def test_layers_keep_their_responsibilities():
    schemas = (ROOT / "app" / "schemas.py").read_text(encoding="utf-8")
    service = (ROOT / "app" / "service.py").read_text(encoding="utf-8")
    router = (ROOT / "app" / "router.py").read_text(encoding="utf-8")

    assert "fastapi" not in schemas.casefold()
    assert "APIRouter" not in service and "HTTPException" not in service
    assert "_BOOKS" not in router

    service_tree = ast.parse(service)
    service_functions = {
        node.name for node in service_tree.body if isinstance(node, ast.FunctionDef)
    }
    assert "search_books" in service_functions
