from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..bsale_client import BsaleClient
from ..config import ACTIVE_STATE, DOCUMENTS_ENDPOINT, TIMEZONE, get_bsale_token
from ..db import get_db_connection

LOGGER = logging.getLogger("bsale.pacas.documents")

PACAS_OFFICE_ID = 6
PACAS_OFFICE_NAME = "PAQUEROS MX"
PACAS_SALE_TYPES = frozenset({10, 45})
PACAS_RETURN_TYPES = frozenset({39, 46})
PACAS_ALLOWED_TYPES = PACAS_SALE_TYPES | PACAS_RETURN_TYPES
MONEY_QUANTUM = Decimal("0.01")


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def money_number(value: Decimal) -> float:
    return float(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def parse_ymd(value: str) -> date:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")
    return parsed


def yesterday_mexico_city() -> date:
    return datetime.now(ZoneInfo(TIMEZONE)).date() - timedelta(days=1)


def bsale_emission_date_range(target_date: date) -> str:
    start_utc = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    end_utc = datetime.combine(target_date, time(23, 59, 59), tzinfo=timezone.utc)
    return f"[{int(start_utc.timestamp())},{int(end_utc.timestamp())}]"


def as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def id_from_href(href: Any, resource_name: str) -> int | None:
    if not href:
        return None
    match = re.search(rf"{re.escape(resource_name)}/(\d+)\.json", str(href))
    return int(match.group(1)) if match else None


def nested_resource_id(payload: dict[str, Any], key: str, resource_name: str) -> int | None:
    nested = payload.get(key) or {}
    if not isinstance(nested, dict):
        return None
    explicit_id = as_int(nested.get("id"))
    if explicit_id is not None:
        return explicit_id
    return id_from_href(nested.get("href"), resource_name)


def document_id(document: dict[str, Any]) -> int | None:
    explicit_id = as_int(document.get("id"))
    return explicit_id if explicit_id is not None else id_from_href(document.get("href"), "documents")


def document_type_id(document: dict[str, Any]) -> int | None:
    return nested_resource_id(document, "document_type", "document_types")


def document_office_id(document: dict[str, Any]) -> int | None:
    return nested_resource_id(document, "office", "offices")


def document_user_id(document: dict[str, Any]) -> int | None:
    return nested_resource_id(document, "user", "users")


def movement_type(type_id: int) -> str | None:
    if type_id in PACAS_SALE_TYPES:
        return "SALE"
    if type_id in PACAS_RETURN_TYPES:
        return "RETURN"
    return None


def emission_date_from_document(document: dict[str, Any]) -> date | None:
    try:
        return datetime.fromtimestamp(int(document.get("emissionDate")), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def seller_name(seller: dict[str, Any]) -> str:
    first_name = str(seller.get("firstName") or "").strip()
    last_name = str(seller.get("lastName") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    return full_name or str(seller.get("name") or seller.get("description") or "N/A").strip()


def normalize_seller(seller: dict[str, Any]) -> dict[str, Any] | None:
    seller_id = as_int(seller.get("id"))
    if seller_id is None:
        seller_id = id_from_href(seller.get("href"), "users")
    if seller_id is None:
        return None
    return {"seller_id": seller_id, "seller_name": seller_name(seller)}


def get_document_sellers(client: BsaleClient, document: dict[str, Any]) -> list[dict[str, Any]]:
    document_id_value = document_id(document)
    if document_id_value is None:
        return []

    sellers_payload = document.get("sellers")
    has_embedded_items = (
        isinstance(sellers_payload, dict)
        and isinstance(sellers_payload.get("items"), list)
    )

    if has_embedded_items:
        raw_items = sellers_payload.get("items") or []
        expected_count = as_int(sellers_payload.get("count"))
        if expected_count is not None and expected_count > len(raw_items):
            raw_items = client.get_all_pages(f"/documents/{document_id_value}/sellers.json")
    else:
        raw_items = client.get_all_pages(f"/documents/{document_id_value}/sellers.json")

    normalized: dict[int, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        seller = normalize_seller(item)
        if seller is not None:
            normalized[seller["seller_id"]] = seller
    return list(normalized.values())


def normalize_document(document: dict[str, Any], sellers: list[dict[str, Any]]) -> dict[str, Any] | None:
    document_id_value = document_id(document)
    type_id = document_type_id(document)
    office_id = document_office_id(document)
    emission_date = emission_date_from_document(document)

    if document_id_value is None or type_id not in PACAS_ALLOWED_TYPES:
        return None
    if office_id != PACAS_OFFICE_ID or emission_date is None:
        return None

    movement = movement_type(type_id)
    if movement is None:
        return None

    return {
        "document_id": document_id_value,
        "emission_date": emission_date,
        "office_id": office_id,
        "document_type_id": type_id,
        "movement_type": movement,
        "document_number": as_int(document.get("number")),
        "serial_number": str(document.get("serialNumber")).strip() if document.get("serialNumber") is not None else None,
        "total_amount": money(document.get("totalAmount")),
        "net_amount": money(document.get("netAmount")),
        "tax_amount": money(document.get("taxAmount")),
        "user_id": document_user_id(document),
        "seller_count": len(sellers),
    }


def extract_documents_for_date(
    client: BsaleClient,
    target_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    emission_range = bsale_emission_date_range(target_date)
    LOGGER.info("Fetching Pacas documents for %s / office %s", target_date.isoformat(), PACAS_OFFICE_ID)

    raw_documents = client.get_all_pages(
        DOCUMENTS_ENDPOINT,
        {
            "emissiondaterange": emission_range,
            "officeid": PACAS_OFFICE_ID,
            "state": ACTIVE_STATE,
            "expand": "[sellers]",
        },
    )

    documents: list[dict[str, Any]] = []
    seller_rows: list[dict[str, Any]] = []
    skipped_document_ids: list[int] = []

    for raw_document in raw_documents:
        raw_type_id = document_type_id(raw_document)
        if raw_type_id not in PACAS_ALLOWED_TYPES:
            raw_id = document_id(raw_document)
            if raw_id is not None:
                skipped_document_ids.append(raw_id)
            continue

        sellers = get_document_sellers(client, raw_document)
        normalized_document = normalize_document(raw_document, sellers)
        if normalized_document is None:
            raw_id = document_id(raw_document)
            if raw_id is not None:
                skipped_document_ids.append(raw_id)
            continue

        documents.append(normalized_document)
        for seller in sellers:
            seller_rows.append({"document_id": normalized_document["document_id"], **seller})

    type_counts = Counter(document["document_type_id"] for document in documents)
    missing_seller_documents = [document["document_id"] for document in documents if document["seller_count"] == 0]
    multi_seller_documents = [document["document_id"] for document in documents if document["seller_count"] > 1]

    gross_sales = sum(
        (document["total_amount"] for document in documents if document["movement_type"] == "SALE"),
        Decimal("0.00"),
    )
    returns_amount = sum(
        (document["total_amount"] for document in documents if document["movement_type"] == "RETURN"),
        Decimal("0.00"),
    )

    metadata = {
        "date": target_date.isoformat(),
        "office_id": PACAS_OFFICE_ID,
        "office_name": PACAS_OFFICE_NAME,
        "emissiondaterange": emission_range,
        "documents_read": len(raw_documents),
        "documents_used": len(documents),
        "seller_rows": len(seller_rows),
        "document_type_counts": {str(key): value for key, value in sorted(type_counts.items())},
        "totals": {
            "gross_sales": money_number(money(gross_sales)),
            "returns_amount": money_number(money(returns_amount)),
            "net_sales": money_number(money(gross_sales - returns_amount)),
        },
        "validation": {
            "status": "PASS" if not missing_seller_documents and not multi_seller_documents else "WARNING",
            "missing_seller_documents": missing_seller_documents,
            "multi_seller_documents": multi_seller_documents,
            "skipped_document_ids": skipped_document_ids,
        },
    }
    return documents, seller_rows, metadata


def persist_documents(
    documents: list[dict[str, Any]],
    seller_rows: list[dict[str, Any]],
) -> tuple[int, int]:
    if not documents:
        return 0, 0

    document_sql = """
        INSERT INTO public.pacas_documents (
            document_id, emission_date, office_id, document_type_id,
            movement_type, document_number, serial_number, total_amount,
            net_amount, tax_amount, user_id, seller_count, synced_at
        ) VALUES (
            %(document_id)s, %(emission_date)s, %(office_id)s,
            %(document_type_id)s, %(movement_type)s, %(document_number)s,
            %(serial_number)s, %(total_amount)s, %(net_amount)s,
            %(tax_amount)s, %(user_id)s, %(seller_count)s, NOW()
        )
        ON CONFLICT (document_id) DO UPDATE SET
            emission_date = EXCLUDED.emission_date,
            office_id = EXCLUDED.office_id,
            document_type_id = EXCLUDED.document_type_id,
            movement_type = EXCLUDED.movement_type,
            document_number = EXCLUDED.document_number,
            serial_number = EXCLUDED.serial_number,
            total_amount = EXCLUDED.total_amount,
            net_amount = EXCLUDED.net_amount,
            tax_amount = EXCLUDED.tax_amount,
            user_id = EXCLUDED.user_id,
            seller_count = EXCLUDED.seller_count,
            synced_at = NOW()
    """

    delete_sellers_sql = "DELETE FROM public.pacas_document_sellers WHERE document_id = %(document_id)s"

    seller_sql = """
        INSERT INTO public.pacas_document_sellers (
            document_id, seller_id, seller_name, synced_at
        ) VALUES (
            %(document_id)s, %(seller_id)s, %(seller_name)s, NOW()
        )
        ON CONFLICT (document_id, seller_id) DO UPDATE SET
            seller_name = EXCLUDED.seller_name,
            synced_at = NOW()
    """

    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany(document_sql, documents)
            cursor.executemany(
                delete_sellers_sql,
                [{"document_id": document["document_id"]} for document in documents],
            )
            if seller_rows:
                cursor.executemany(seller_sql, seller_rows)

    return len(documents), len(seller_rows)


def json_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        **document,
        "emission_date": document["emission_date"].isoformat(),
        "total_amount": money_number(document["total_amount"]),
        "net_amount": money_number(document["net_amount"]),
        "tax_amount": money_number(document["tax_amount"]),
    }


def write_output(
    target_date: date,
    documents: list[dict[str, Any]],
    seller_rows: list[dict[str, Any]],
    metadata: dict[str, Any],
    output_dir: Path,
    *,
    database_documents_written: int,
    database_sellers_written: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"pacas_documents_{target_date.isoformat()}.json"
    payload = {
        "status": "OK",
        **metadata,
        "database_documents_written": database_documents_written,
        "database_sellers_written": database_sellers_written,
        "documents": [json_document(document) for document in documents],
        "sellers": seller_rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract Pacas MX Bsale documents and sellers for one date.")
    parser.add_argument(
        "--date",
        help="Date to extract in YYYY-MM-DD. Defaults to yesterday in America/Mexico_City.",
    )
    parser.add_argument("--write-db", action="store_true", help="Upsert extracted rows into Neon/PostgreSQL.")
    parser.add_argument("--output-dir", default="output", help="Directory for the JSON audit artifact.")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    args = build_parser().parse_args()
    target_date = parse_ymd(args.date) if args.date else yesterday_mexico_city()

    client = BsaleClient(get_bsale_token())
    documents, seller_rows, metadata = extract_documents_for_date(client, target_date)

    database_documents_written = 0
    database_sellers_written = 0
    if args.write_db:
        database_documents_written, database_sellers_written = persist_documents(documents, seller_rows)

    output_path = write_output(
        target_date,
        documents,
        seller_rows,
        metadata,
        Path(args.output_dir),
        database_documents_written=database_documents_written,
        database_sellers_written=database_sellers_written,
    )

    print(
        json.dumps(
            {
                "status": "OK",
                **metadata,
                "database_documents_written": database_documents_written,
                "database_sellers_written": database_sellers_written,
                "output_file": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
