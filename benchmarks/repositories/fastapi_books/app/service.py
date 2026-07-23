"""Book query business logic."""

from .schemas import BookOut


_BOOKS = (
    BookOut(id=1, title="Python Engineering", author="Jane Doe"),
    BookOut(id=2, title="Domain-Driven Design", author="Eric Evans"),
    BookOut(id=3, title="Fluent Python", author="Luciano Ramalho"),
)


def list_books() -> list[BookOut]:
    return list(_BOOKS)


def get_book(book_id: int) -> BookOut | None:
    return next((book for book in _BOOKS if book.id == book_id), None)
