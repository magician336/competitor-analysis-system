"""Order CSV normalization."""

from collections.abc import Iterable, Mapping


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def clean_order_rows(
    rows: Iterable[Mapping[str, object]],
) -> list[dict[str, str]]:
    return [
        {
            "order_id": _clean_cell(row.get("order_id")),
            "customer_id": _clean_cell(row.get("customer_id")),
            "status": _clean_cell(row.get("status")),
        }
        for row in rows
    ]
