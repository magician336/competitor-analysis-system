from __future__ import annotations


def parse_kv_line(line: str) -> dict[str, str]:
    """Parse a comma-separated key=value line under the frozen task contract."""

    if not isinstance(line, str):
        raise TypeError("line must be a string")
    if not line.strip():
        return {}

    parsed: dict[str, str] = {}
    for raw_item in line.split(","):
        item = raw_item.strip()
        if not item or "=" not in item:
            raise ValueError("every item must contain a key and '='")
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError("keys must not be empty")
        if key in parsed:
            raise ValueError(f"duplicate key: {key}")
        parsed[key] = value
    return parsed

