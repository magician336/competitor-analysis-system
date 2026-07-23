"""Customer CSV normalization."""

from collections.abc import Iterable, Mapping


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def clean_customer_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[dict[str, str]]:
    return [
        {
            "customer_id": _clean_cell(row.get("customer_id")),
            "email": _clean_cell(row.get("email")),
            "country": _clean_cell(row.get("country")),
        }
        for row in rows
    ]
