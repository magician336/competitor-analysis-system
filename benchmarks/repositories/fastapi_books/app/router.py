"""HTTP routes for books."""

from fastapi import APIRouter, HTTPException

from .schemas import BookOut
from .service import get_book, list_books


router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=list[BookOut])
def read_books() -> list[BookOut]:
    return list_books()


@router.get("/{book_id}", response_model=BookOut)
def read_book(book_id: int) -> BookOut:
    book = get_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return book
