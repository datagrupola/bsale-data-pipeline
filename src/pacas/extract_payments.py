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

from ..bsale_client import BsaleClient
from ..config import ACTIVE_STATE, PAYMENTS_ENDPOINT, get_bsale_token
from ..db import get_db_connection
from ..extract_daily_sales import (
    bsale_record_date,
    payment_amount,
    payment_document_id,
    payment_state,
    payment_type_id,
)
from .extract_documents import (
    PACAS_OFFICE_ID,
    PACAS_OFFICE_NAME,
    parse_ymd,
    yesterday_mexico_city,
)

LOGGER = logging.getLogger("bsale.pacas.payments")
MONEY_QUANTUM = Decimal("0.01")


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


def load_sale_documents(target_date: date) -> list[dict[str, Any]]:
    with get_db_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT document_id, total_amount
                FROM public.pacas_documents
                WHERE emission_date = %s
                  AND office_id = %s
                  AND movement_type = 'SALE'
                ORDER BY document_id
                """,
                (target_date, PACAS_OFFICE_ID),
            )
            return list(cursor.fetchall())


def load_payment_type_names(client: BsaleClient) -> dict[int, str]:
    rows = client.get_all_pages("/payment_types.json", {"state": ACTIVE_STATE})
    result: dict[int, str] = {}
    for row in rows:
        try:
            type_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(row.get("name") or f"PAYMENT_TYPE_{type_id}").strip()
        result[type_id] = name
    return result


def extract_payments_for_date(
    client: BsaleClient,
    target_date: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sale_documents = load_sale_documents(target_date)
    sale_totals = {
        int(row["document_id"]): money(row["total_amount"])
        for row in sale_documents
    }
    sale_document_ids = set(sale_totals)
    gross_sales = money(sum(sale_totals.values(), Decimal("0.00")))

    record_date = bsale_record_date(target_date)
    LOGGER.info(
        "Fetching Pacas payments for %s / office %s",
        target_date.isoformat(),
        PACAS_OFFICE_ID,
    )
    all_payments = client.get_all_pages(
        PAYMENTS_ENDPOINT,
        {"recorddate": record_date},
    )
    payment_type_names = load_payment_type_names(client)

    totals_by_type: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    totals_by_document: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))
    relevant_payments = 0
    unknown_payment_type_ids: set[int] = set()

    for payment in all_payments:
        if payment_state(payment) != ACTIVE_STATE:
            continue

        linked_document_id = payment_document_id(payment)
        if linked_document_id not in sale_document_ids:
            continue

        type_id = payment_type_id(payment)
        if type_id is None:
            continue

        amount = payment_amount(payment)
        totals_by_type[type_id] += amount
        totals_by_document[linked_document_id] += amount
        relevant_payments += 1

        if type_id not in payment_type_names:
            unknown_payment_type_ids.add(type_id)

    rows = [
        {
            "sale_date": target_date,
            "office_id": PACAS_OFFICE_ID,
            "payment_type_id": type_id,
            "payment_type_name": payment_type_names.get(
                type_id,
                f"PAYMENT_TYPE_{type_id}",
            ),
            "amount": money(amount),
        }
        for type_id, amount in sorted(totals_by_type.items())
        if money(amount) != Decimal("0.00")
    ]

    payments_total = money(
        sum((row["amount"] for row in rows), Decimal("0.00"))
    )
    total_difference = money(payments_total - gross_sales)

    document_mismatches: list[dict[str, Any]] = []
    for document_id, document_total in sale_totals.items():
        payment_total = money(totals_by_document.get(document_id, Decimal("0.00")))
        difference = money(payment_total - document_total)
        if difference != Decimal("0.00"):
            document_mismatches.append(
                {
                    "document_id": document_id,
                    "document_total": money_number(document_total),
                    "payment_total": money_number(payment_total),
                    "difference": money_number(difference),
                }
            )

    metadata = {
        "date": target_date.isoformat(),
        "office_id": PACAS_OFFICE_ID,
        "office_name": PACAS_OFFICE_NAME,
        "recorddate": record_date,
        "sale_documents": len(sale_documents),
        "payments_read": len(all_payments),
        "relevant_payments": relevant_payments,
        "payment_types_used": len(rows),
        "gross_sales": money_number(gross_sales),
        "payments_total": money_number(payments_total),
        "validation": {
            "status": (
                "PASS"
                if total_difference == Decimal("0.00")
                and not document_mismatches
                and not unknown_payment_type_ids
                else "WARNING"
            ),
            "difference": money_number(total_difference),
            "document_mismatches": document_mismatches,
            "unknown_payment_type_ids": sorted(unknown_payment_type_ids),
        },
    }
    return rows, metadata


def persist_payments(target_date: date, rows: list[dict[str, Any]]) -> int:
    delete_sql = """
        DELETE FROM public.pacas_payments_daily
        WHERE sale_date = %s AND office_id = %s
    """
    insert_sql = """
        INSERT INTO public.pacas_payments_daily (
            sale_date,
            office_id,
            payment_type_id,
            payment_type_name,
            amount,
            synced_at
        ) VALUES (
            %(sale_date)s,
            %(office_id)s,
            %(payment_type_id)s,
            %(payment_type_name)s,
            %(amount)s,
            NOW()
        )
        ON CONFLICT (sale_date, office_id, payment_type_id) DO UPDATE SET
            payment_type_name = EXCLUDED.payment_type_name,
            amount = EXCLUDED.amount,
            synced_at = NOW()
    """

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(delete_sql, (target_date, PACAS_OFFICE_ID))
            if rows:
                cursor.executemany(insert_sql, rows)

    return len(rows)


def json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "sale_date": row["sale_date"].isoformat(),
        "amount": money_number(row["amount"]),
    }


def write_output(
    target_date: date,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
    output_dir: Path,
    database_rows_written: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"pacas_payments_{target_date.isoformat()}.json"
    payload = {
        "status": "OK",
        **metadata,
        "database_rows_written": database_rows_written,
        "rows": [json_row(row) for row in rows],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Pacas MX payment methods and persist daily totals."
    )
    parser.add_argument(
        "--date",
        help=(
            "Date in YYYY-MM-DD. Defaults to yesterday in America/Mexico_City."
        ),
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Replace payment totals for the date in Neon/PostgreSQL.",
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
    rows, metadata = extract_payments_for_date(client, target_date)

    database_rows_written = 0
    if args.write_db:
        database_rows_written = persist_payments(target_date, rows)

    output_path = write_output(
        target_date,
        rows,
        metadata,
        Path(args.output_dir),
        database_rows_written,
    )

    print(
        json.dumps(
            {
                "status": "OK",
                **metadata,
                "database_rows_written": database_rows_written,
                "rows": [json_row(row) for row in rows],
                "output_file": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
