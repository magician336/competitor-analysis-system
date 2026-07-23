from __future__ import annotations


def no_whitespace_normalization(line: str) -> dict[str, str]:
    if not isinstance(line, str):
        raise TypeError("line must be a string")
    if not line.strip():
        return {}
    result: dict[str, str] = {}
    for item in line.split(","):
        if "=" not in item:
            raise ValueError("missing equals")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError("empty key")
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def ignore_missing_equals(line: str) -> dict[str, str]:
    if not isinstance(line, str):
        raise TypeError("line must be a string")
    result: dict[str, str] = {}
    for item in line.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def overwrite_duplicate_key(line: str) -> dict[str, str]:
    if not isinstance(line, str):
        raise TypeError("line must be a string")
    if not line.strip():
        return {}
    result: dict[str, str] = {}
    for item in line.split(","):
        if "=" not in item:
            raise ValueError("missing equals")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("empty key")
        result[key] = value.strip()
    return result


def reject_unicode(line: str) -> dict[str, str]:
    if not isinstance(line, str):
        raise TypeError("line must be a string")
    line.encode("ascii")
    if not line.strip():
        return {}
    return {
        key.strip(): value.strip()
        for key, value in (item.split("=", 1) for item in line.split(","))
    }


def accept_empty_key(line: str) -> dict[str, str]:
    if not isinstance(line, str):
        raise TypeError("line must be a string")
    if not line.strip():
        return {}
    result: dict[str, str] = {}
    for item in line.split(","):
        if "=" not in item:
            raise ValueError("missing equals")
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


MUTANTS = {
    "no_whitespace_normalization": no_whitespace_normalization,
    "ignore_missing_equals": ignore_missing_equals,
    "overwrite_duplicate_key": overwrite_duplicate_key,
    "reject_unicode": reject_unicode,
    "accept_empty_key": accept_empty_key,
}

