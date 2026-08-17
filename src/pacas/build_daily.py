from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from ..db import get_db_connection
from .extract_documents import (
    PACAS_OFFICE_ID,
    PACAS_OFFICE_NAME,
    parse_ymd,
    yesterday_mexico_city,
)

LOGGER = logging.getLogger("bsale.pacas.daily")
MONEY_QUANTUM = Decimal("0.01")
RESOLVED = "RESOLVED"


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    except Exception:
        return Decimal("0.00")


def money_number(value: Decimal) -> float:
    return float(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def load_source_rows(target_date: date) -> dict[str, Any]:
    with get_db_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT document_id, movement_type, total_amount, seller_count
                FROM public.pacas_documents
                WHERE emission_date = %s AND office_id = %s
                ORDER BY document_id
                """,
                (target_date, PACAS_OFFICE_ID),
            )
            documents = list(cursor.fetchall())

            cursor.execute(
                """
                SELECT ds.document_id, ds.seller_id, ds.seller_name
                FROM public.pacas_document_sellers ds
                JOIN public.pacas_documents d ON d.document_id = ds.document_id
                WHERE d.emission_date = %s AND d.office_id = %s
                ORDER BY ds.document_id, ds.seller_id
                """,
                (target_date, PACAS_OFFICE_ID),
            )
            sellers = list(cursor.fetchall())

            cursor.execute(
                """
                SELECT
                    ra.return_document_id,
                    ra.return_detail_id,
                    ra.original_seller_id,
                    ra.original_seller_name,
                    ra.return_amount,
                    ra.resolution_status
                FROM public.pacas_return_allocations ra
                JOIN public.pacas_documents d
                  ON d.document_id = ra.return_document_id
                WHERE d.emission_date = %s AND d.office_id = %s
                ORDER BY ra.return_document_id, ra.return_detail_id
                """,
                (target_date, PACAS_OFFICE_ID),
            )
            allocations = list(cursor.fetchall())

    return {
        "documents": documents,
        "sellers": sellers,
        "allocations": allocations,
    }


def build_daily_rows(
    target_date: date,
    source: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    documents = source["documents"]
    sellers = source["sellers"]
    allocations = source["allocations"]

    sellers_by_document: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for seller in sellers:
        sellers_by_document[int(seller["document_id"])].append(seller)

    gross_sales = Decimal("0.00")
    returns_amount = Decimal("0.00")
    tickets_count = 0

    seller_metrics: dict[int, dict[str, Any]] = {}
    sale_allocation_issues: list[dict[str, Any]] = []

    for document in documents:
        current_document_id = int(document["document_id"])
        movement = document["movement_type"]
        amount = money(document["total_amount"])

        if movement == "SALE":
            gross_sales += amount
            tickets_count += 1
            document_sellers = sellers_by_document.get(current_document_id, [])

            if len(document_sellers) != 1:
                sale_allocation_issues.append(
                    {
                        "document_id": current_document_id,
                        "seller_count": len(document_sellers),
                    }
                )
                continue

            seller = document_sellers[0]
            seller_id = int(seller["seller_id"])
            metric = seller_metrics.setdefault(
                seller_id,
                {
                    "sale_date": target_date,
                    "office_id": PACAS_OFFICE_ID,
                    "seller_id": seller_id,
                    "seller_name": seller["seller_name"],
                    "gross_sales": Decimal("0.00"),
                    "returns_amount": Decimal("0.00"),
                    "net_sales": Decimal("0.00"),
                    "tickets_count": 0,
                },
            )
            metric["seller_name"] = seller["seller_name"]
            metric["gross_sales"] += amount
            metric["tickets_count"] += 1

        elif movement == "RETURN":
            returns_amount += amount

    unresolved_allocations: list[dict[str, Any]] = []
    resolved_return_total = Decimal("0.00")

    return_documents = {
        int(document["document_id"])
        for document in documents
        if document["movement_type"] == "RETURN"
    }
    return_documents_with_allocations: set[int] = set()

    for allocation in allocations:
        return_document_id = int(allocation["return_document_id"])
        return_documents_with_allocations.add(return_document_id)

        if allocation["resolution_status"] != RESOLVED:
            unresolved_allocations.append(
                {
                    "return_document_id": return_document_id,
                    "return_detail_id": int(allocation["return_detail_id"]),
                    "resolution_status": allocation["resolution_status"],
                }
            )
            continue

        seller_id_value = allocation["original_seller_id"]
        seller_name = allocation["original_seller_name"]

        if seller_id_value is None or not seller_name:
            unresolved_allocations.append(
                {
                    "return_document_id": return_document_id,
                    "return_detail_id": int(allocation["return_detail_id"]),
                    "resolution_status": "ORIGINAL_SELLER_NOT_FOUND",
                }
            )
            continue

        seller_id = int(seller_id_value)
        amount = money(allocation["return_amount"])
        resolved_return_total += amount

        metric = seller_metrics.setdefault(
            seller_id,
            {
                "sale_date": target_date,
                "office_id": PACAS_OFFICE_ID,
                "seller_id": seller_id,
                "seller_name": seller_name,
                "gross_sales": Decimal("0.00"),
                "returns_amount": Decimal("0.00"),
                "net_sales": Decimal("0.00"),
                "tickets_count": 0,
            },
        )
        metric["seller_name"] = seller_name
        metric["returns_amount"] += amount

    returns_without_allocations = sorted(
        return_documents - return_documents_with_allocations
    )

    gross_sales = money(gross_sales)
    returns_amount = money(returns_amount)
    net_sales = money(gross_sales - returns_amount)
    resolved_return_total = money(resolved_return_total)

    seller_rows: list[dict[str, Any]] = []
    for seller_id in sorted(seller_metrics):
        metric = seller_metrics[seller_id]
        metric["gross_sales"] = money(metric["gross_sales"])
        metric["returns_amount"] = money(metric["returns_amount"])
        metric["net_sales"] = money(
            metric["gross_sales"] - metric["returns_amount"]
        )
        seller_rows.append(metric)

    seller_gross_total = money(
        sum((row["gross_sales"] for row in seller_rows), Decimal("0.00"))
    )
    seller_returns_total = money(
        sum((row["returns_amount"] for row in seller_rows), Decimal("0.00"))
    )
    seller_net_total = money(
        sum((row["net_sales"] for row in seller_rows), Decimal("0.00"))
    )

    unresolved_returns_count = len(
        {
            item["return_document_id"]
            for item in unresolved_allocations
        }
        | set(returns_without_allocations)
    )

    daily_row = {
        "sale_date": target_date,
        "office_id": PACAS_OFFICE_ID,
        "gross_sales": gross_sales,
        "returns_amount": returns_amount,
        "net_sales": net_sales,
        "tickets_count": tickets_count,
        "unresolved_returns_count": unresolved_returns_count,
    }

    gross_difference = money(seller_gross_total - gross_sales)
    returns_difference = money(seller_returns_total - returns_amount)
    net_difference = money(seller_net_total - net_sales)

    validation_status = (
        "PASS"
        if not sale_allocation_issues
        and not unresolved_allocations
        and not returns_without_allocations
        and gross_difference == Decimal("0.00")
        and returns_difference == Decimal("0.00")
        and net_difference == Decimal("0.00")
        else "WARNING"
    )

    metadata = {
        "date": target_date.isoformat(),
        "office_id": PACAS_OFFICE_ID,
        "office_name": PACAS_OFFICE_NAME,
        "documents_read": len(documents),
        "seller_rows": len(seller_rows),
        "daily": {
            "gross_sales": money_number(gross_sales),
            "returns_amount": money_number(returns_amount),
            "net_sales": money_number(net_sales),
            "tickets_count": tickets_count,
            "unresolved_returns_count": unresolved_returns_count,
        },
        "validation": {
            "status": validation_status,
            "sale_allocation_issues": sale_allocation_issues,
            "unresolved_allocations": unresolved_allocations,
            "returns_without_allocations": returns_without_allocations,
            "resolved_return_total": money_number(resolved_return_total),
            "seller_gross_total": money_number(seller_gross_total),
            "seller_returns_total": money_number(seller_returns_total),
            "seller_net_total": money_number(seller_net_total),
            "gross_difference": money_number(gross_difference),
            "returns_difference": money_number(returns_difference),
            "net_difference": money_number(net_difference),
        },
    }

    return daily_row, seller_rows, metadata


def persist_daily_rows(
    daily_row: dict[str, Any],
    seller_rows: list[dict[str, Any]],
) -> tuple[int, int]:
    daily_sql = """
        INSERT INTO public.pacas_daily_sales (
            sale_date, office_id, gross_sales, returns_amount, net_sales,
            tickets_count, unresolved_returns_count, synced_at
        ) VALUES (
            %(sale_date)s, %(office_id)s, %(gross_sales)s,
            %(returns_amount)s, %(net_sales)s, %(tickets_count)s,
            %(unresolved_returns_count)s, NOW()
        )
        ON CONFLICT (sale_date, office_id) DO UPDATE SET
            gross_sales = EXCLUDED.gross_sales,
            returns_amount = EXCLUDED.returns_amount,
            net_sales = EXCLUDED.net_sales,
            tickets_count = EXCLUDED.tickets_count,
            unresolved_returns_count = EXCLUDED.unresolved_returns_count,
            synced_at = NOW()
    """

    delete_sellers_sql = """
        DELETE FROM public.pacas_seller_daily
        WHERE sale_date = %s AND office_id = %s
    """

    seller_sql = """
        INSERT INTO public.pacas_seller_daily (
            sale_date, office_id, seller_id, seller_name,
            gross_sales, returns_amount, net_sales, tickets_count, synced_at
        ) VALUES (
            %(sale_date)s, %(office_id)s, %(seller_id)s, %(seller_name)s,
            %(gross_sales)s, %(returns_amount)s, %(net_sales)s,
            %(tickets_count)s, NOW()
        )
        ON CONFLICT (sale_date, office_id, seller_id) DO UPDATE SET
            seller_name = EXCLUDED.seller_name,
            gross_sales = EXCLUDED.gross_sales,
            returns_amount = EXCLUDED.returns_amount,
            net_sales = EXCLUDED.net_sales,
            tickets_count = EXCLUDED.tickets_count,
            synced_at = NOW()
    """

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(daily_sql, daily_row)
            cursor.execute(
                delete_sellers_sql,
                (daily_row["sale_date"], daily_row["office_id"]),
            )
            if seller_rows:
                cursor.executemany(seller_sql, seller_rows)

    return 1, len(seller_rows)


def json_daily_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "sale_date": row["sale_date"].isoformat(),
        "gross_sales": money_number(row["gross_sales"]),
        "returns_amount": money_number(row["returns_amount"]),
        "net_sales": money_number(row["net_sales"]),
    }


def json_seller_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "sale_date": row["sale_date"].isoformat(),
        "gross_sales": money_number(row["gross_sales"]),
        "returns_amount": money_number(row["returns_amount"]),
        "net_sales": money_number(row["net_sales"]),
    }


def write_output(
    target_date: date,
    daily_row: dict[str, Any],
    seller_rows: list[dict[str, Any]],
    metadata: dict[str, Any],
    output_dir: Path,
    *,
    database_daily_rows_written: int,
    database_seller_rows_written: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"pacas_daily_{target_date.isoformat()}.json"

    payload = {
        "status": "OK",
        **metadata,
        "database_daily_rows_written": database_daily_rows_written,
        "database_seller_rows_written": database_seller_rows_written,
        "daily_row": json_daily_row(daily_row),
        "seller_daily_rows": [json_seller_row(row) for row in seller_rows],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build Pacas MX daily and seller aggregates from Neon."
    )
    parser.add_argument(
        "--date",
        help=(
            "Date to aggregate in YYYY-MM-DD. Defaults to yesterday in "
            "America/Mexico_City."
        ),
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Upsert aggregates into Neon/PostgreSQL.",
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

    LOGGER.info(
        "Building Pacas daily aggregates for %s / office %s",
        target_date.isoformat(),
        PACAS_OFFICE_ID,
    )

    source = load_source_rows(target_date)
    daily_row, seller_rows, metadata = build_daily_rows(target_date, source)

    database_daily_rows_written = 0
    database_seller_rows_written = 0
    if args.write_db:
        (
            database_daily_rows_written,
            database_seller_rows_written,
        ) = persist_daily_rows(daily_row, seller_rows)

    output_path = write_output(
        target_date,
        daily_row,
        seller_rows,
        metadata,
        Path(args.output_dir),
        database_daily_rows_written=database_daily_rows_written,
        database_seller_rows_written=database_seller_rows_written,
    )

    print(
        json.dumps(
            {
                "status": "OK",
                **metadata,
                "database_daily_rows_written": database_daily_rows_written,
                "database_seller_rows_written": database_seller_rows_written,
                "output_file": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
