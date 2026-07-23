"""API response schemas."""

from pydantic import BaseModel, ConfigDict


class BookOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    title: str
    author: str
