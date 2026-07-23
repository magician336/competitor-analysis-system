from __future__ import annotations

import os


def list_file(user_value: str):
    """Deliberately vulnerable starter implementation."""

    return os.system("ls " + user_value)
