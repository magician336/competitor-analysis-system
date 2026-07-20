"""Application entry point."""

from fastapi import FastAPI

from .router import router


app = FastAPI(title="Books starter")
app.include_router(router)
