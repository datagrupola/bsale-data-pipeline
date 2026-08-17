from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..bsale_client import BsaleClient
from ..config import ACTIVE_STATE, DOCUMENTS_ENDPOINT, get_bsale_token
from ..db import get_db_connection
from .extract_documents import (
    PACAS_OFFICE_ID,
    PACAS_OFFICE_NAME,
    PACAS_RETURN_TYPES,
    as_int,
    bsale_emission_date_range,
    document_id,
    document_type_id,
    get_document_sellers,
    id_from_href,
    money,
    money_number,
    parse_ymd,
    yesterday_mexico_city,
)

LOGGER = logging.getLogger("bsale.pacas.returns")

RESOLVED = "RESOLVED"
MISSING_RELATED_DETAIL = "MISSING_RELATED_DETAIL"
ORIGINAL_DOCUMENT_NOT_FOUND = "ORIGINAL_DOCUMENT_NOT_FOUND"
ORIGINAL_SELLER_NOT_FOUND = "ORIGINAL_SELLER_NOT_FOUND"
MULTIPLE_ORIGINAL_SELLERS = "MULTIPLE_ORIGINAL_SELLERS"


def detail_id(detail: dict[str, Any]) -> int | None:
    explicit_id = as_int(detail.get("id"))
    if explicit_id is not None:
        return explicit_id
    return id_from_href(detail.get("href"), "details")


def related_detail_id(detail: dict[str, Any]) -> int | None:
    value = (
        detail.get("relatedDetailId")
        if "relatedDetailId" in detail
        else detail.get("related_detail_id")
    )
    parsed = as_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def detail_total_amount(detail: dict[str, Any]) -> Decimal:
    for key in ("totalAmount", "total_amount", "totalAmountWD"):
        if detail.get(key) is not None:
            return money(detail.get(key))

    quantity = money(detail.get("quantity"))
    unit_total = money(
        detail.get("totalUnitValue")
        if detail.get("totalUnitValue") is not None
        else detail.get("total_unit_value")
    )
    if quantity and unit_total:
        return money(quantity * unit_total)

    net = money(detail.get("netAmount") or detail.get("net_amount"))
    tax = money(detail.get("taxAmount") or detail.get("tax_amount"))
    return money(net + tax)


def get_return_documents(
    client: BsaleClient,
    target_date: date,
) -> list[dict[str, Any]]:
    documents = client.get_all_pages(
        DOCUMENTS_ENDPOINT,
        {
            "emissiondaterange": bsale_emission_date_range(target_date),
            "officeid": PACAS_OFFICE_ID,
            "state": ACTIVE_STATE,
        },
    )
    return [
        document
        for document in documents
        if document_type_id(document) in PACAS_RETURN_TYPES
    ]


def get_return_details(
    client: BsaleClient,
    return_document_id: int,
) -> list[dict[str, Any]]:
    return client.get_all_pages(
        f"/documents/{return_document_id}/details.json"
    )


def find_original_document(
    client: BsaleClient,
    related_detail_id_value: int,
) -> dict[str, Any] | None:
    documents = client.get_all_pages(
        DOCUMENTS_ENDPOINT,
        {
            "detailid": related_detail_id_value,
            "expand": "[sellers]",
        },
    )
    if len(documents) != 1:
        return None
    return documents[0]


def resolve_return_detail(
    client: BsaleClient,
    return_document_id: int,
    detail: dict[str, Any],
) -> dict[str, Any] | None:
    return_detail_id = detail_id(detail)
    if return_detail_id is None:
        return None

    related_id = related_detail_id(detail)
    amount = detail_total_amount(detail)

    row: dict[str, Any] = {
        "return_document_id": return_document_id,
        "return_detail_id": return_detail_id,
        "related_detail_id": related_id,
        "original_document_id": None,
        "original_seller_id": None,
        "original_seller_name": None,
        "return_amount": amount,
        "resolution_status": MISSING_RELATED_DETAIL,
    }

    if related_id is None:
        return row

    original_document = find_original_document(client, related_id)
    if original_document is None:
        row["resolution_status"] = ORIGINAL_DOCUMENT_NOT_FOUND
        return row

    original_document_id = document_id(original_document)
    row["original_document_id"] = original_document_id

    sellers = get_document_sellers(client, original_document)
    if not sellers:
        row["resolution_status"] = ORIGINAL_SELLER_NOT_FOUND
        return row

    if len(sellers) > 1:
        row["resolution_status"] = MULTIPLE_ORIGINAL_SELLERS
        return row

    seller = sellers[0]
    row["original_seller_id"] = seller["seller_id"]
    row["original_seller_name"] = seller["seller_name"]
    row["resolution_status"] = RESOLVED
    return row


def resolve_returns_for_date(
    client: BsaleClient,
    target_date: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    LOGGER.info(
        "Resolving Pacas returns for %s / office %s",
        target_date.isoformat(),
        PACAS_OFFICE_ID,
    )

    return_documents = get_return_documents(client, target_date)
    allocations: list[dict[str, Any]] = []
    document_checks: list[dict[str, Any]] = []

    for return_document in return_documents:
        return_document_id = document_id(return_document)
        if return_document_id is None:
            continue

        details = get_return_details(client, return_document_id)
        rows_for_document: list[dict[str, Any]] = []

        for detail in details:
            row = resolve_return_detail(client, return_document_id, detail)
            if row is not None:
                rows_for_document.append(row)
                allocations.append(row)

        document_total = money(return_document.get("totalAmount"))
        details_total = money(
            sum(
                (row["return_amount"] for row in rows_for_document),
                Decimal("0.00"),
            )
        )
        difference = money(details_total - document_total)
        document_checks.append(
            {
                "return_document_id": return_document_id,
                "document_total": money_number(document_total),
                "details_total": money_number(details_total),
                "difference": money_number(difference),
                "details_count": len(rows_for_document),
            }
        )

    status_counts = Counter(
        allocation["resolution_status"] for allocation in allocations
    )
    unresolved = [
        allocation
        for allocation in allocations
        if allocation["resolution_status"] != RESOLVED
    ]
    total_mismatches = [
        check
        for check in document_checks
        if abs(check["difference"]) > 0.01
    ]

    metadata = {
        "date": target_date.isoformat(),
        "office_id": PACAS_OFFICE_ID,
        "office_name": PACAS_OFFICE_NAME,
        "return_documents": len(return_documents),
        "allocations": len(allocations),
        "resolution_status_counts": dict(sorted(status_counts.items())),
        "validation": {
            "status": (
                "PASS"
                if not unresolved and not total_mismatches
                else "WARNING"
            ),
            "unresolved_count": len(unresolved),
            "unresolved": [
                {
                    "return_document_id": row["return_document_id"],
                    "return_detail_id": row["return_detail_id"],
                    "related_detail_id": row["related_detail_id"],
                    "resolution_status": row["resolution_status"],
                }
                for row in unresolved
            ],
            "document_total_checks": document_checks,
            "document_total_mismatches": total_mismatches,
        },
    }
    return allocations, metadata


def persist_return_allocations(
    allocations: list[dict[str, Any]],
    *,
    return_document_ids: list[int],
) -> int:
    if not return_document_ids:
        return 0

    delete_sql = """
        DELETE FROM public.pacas_return_allocations
        WHERE return_document_id = %(return_document_id)s
    """

    insert_sql = """
        INSERT INTO public.pacas_return_allocations (
            return_document_id,
            return_detail_id,
            related_detail_id,
            original_document_id,
            original_seller_id,
            original_seller_name,
            return_amount,
            resolution_status,
            synced_at
        ) VALUES (
            %(return_document_id)s,
            %(return_detail_id)s,
            %(related_detail_id)s,
            %(original_document_id)s,
            %(original_seller_id)s,
            %(original_seller_name)s,
            %(return_amount)s,
            %(resolution_status)s,
            NOW()
        )
        ON CONFLICT (return_document_id, return_detail_id) DO UPDATE SET
            related_detail_id = EXCLUDED.related_detail_id,
            original_document_id = EXCLUDED.original_document_id,
            original_seller_id = EXCLUDED.original_seller_id,
            original_seller_name = EXCLUDED.original_seller_name,
            return_amount = EXCLUDED.return_amount,
            resolution_status = EXCLUDED.resolution_status,
            synced_at = NOW()
    """

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                delete_sql,
                [
                    {"return_document_id": return_document_id}
                    for return_document_id in return_document_ids
                ],
            )
            if allocations:
                cursor.executemany(insert_sql, allocations)

    return len(allocations)


def json_allocation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "return_amount": money_number(row["return_amount"]),
    }


def write_output(
    target_date: date,
    allocations: list[dict[str, Any]],
    metadata: dict[str, Any],
    output_dir: Path,
    *,
    database_rows_written: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"pacas_returns_{target_date.isoformat()}.json"
    payload = {
        "status": "OK",
        **metadata,
        "database_rows_written": database_rows_written,
        "rows": [json_allocation(row) for row in allocations],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve Pacas MX return details to the seller of the original sale."
        )
    )
    parser.add_argument(
        "--date",
        help=(
            "Date to resolve in YYYY-MM-DD. Defaults to yesterday in "
            "America/Mexico_City."
        ),
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Replace return allocations for the date in Neon/PostgreSQL.",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for the JSON audit artifact.",
    )
    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    args = build_parser().parse_args()
    target_date = parse_ymd(args.date) if args.date else yesterday_mexico_city()

    client = BsaleClient(get_bsale_token())
    allocations, metadata = resolve_returns_for_date(client, target_date)

    return_document_ids = sorted(
        {allocation["return_document_id"] for allocation in allocations}
    )
    if not return_document_ids and metadata["return_documents"]:
        # A return document without a normalizable detail must not leave stale rows.
        return_documents = get_return_documents(client, target_date)
        return_document_ids = sorted(
            {
                document_id(document)
                for document in return_documents
                if document_id(document) is not None
            }
        )

    database_rows_written = 0
    if args.write_db:
        database_rows_written = persist_return_allocations(
            allocations,
            return_document_ids=return_document_ids,
        )

    output_path = write_output(
        target_date,
        allocations,
        metadata,
        Path(args.output_dir),
        database_rows_written=database_rows_written,
    )

    print(
        json.dumps(
            {
                "status": "OK",
                **metadata,
                "database_rows_written": database_rows_written,
                "output_file": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
