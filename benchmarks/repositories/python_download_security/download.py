from __future__ import annotations

from pathlib import Path


class DownloadError(Exception):
    pass


def read_download(root: Path, requested: str) -> bytes:
    """Deliberately vulnerable starter implementation."""

    path = root / requested
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DownloadError(str(exc)) from exc
