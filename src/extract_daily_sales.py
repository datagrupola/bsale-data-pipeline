from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .bsale_client import BsaleClient
from .config import (
    ACTIVE_STATE,
    DOCUMENT_TYPES,
    DOCUMENTS_ENDPOINT,
    OFFICES,
    PAYMENT_TYPES,
    PAYMENTS_ENDPOINT,
    TIMEZONE,
    get_bsale_token,
)

LOGGER = logging.getLogger("bsale.daily_sales")
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
    return float(
        value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    )


def parse_ymd(value: str) -> date:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid date '{value}'. Expected YYYY-MM-DD."
        ) from exc

    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")

    return parsed


def yesterday_mexico_city() -> date:
    return (
        datetime.now(ZoneInfo(TIMEZONE)).date()
        - timedelta(days=1)
    )


def bsale_emission_date_range(target_date: date) -> str:
    start_utc = datetime.combine(
        target_date,
        time.min,
        tzinfo=timezone.utc,
    )
    end_utc = datetime.combine(
        target_date,
        time(23, 59, 59),
        tzinfo=timezone.utc,
    )
    return f"[{int(start_utc.timestamp())},{int(end_utc.timestamp())}]"


def bsale_record_date(target_date: date) -> int:
    start_utc = datetime.combine(
        target_date,
        time.min,
        tzinfo=timezone.utc,
    )
    return int(start_utc.timestamp())


def value_from_keys(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def id_from_href(href: Any, resource_name: str) -> int | None:
    if not href:
        return None

    match = re.search(
        rf"{re.escape(resource_name)}/(\d+)\.json",
        str(href),
    )
    return int(match.group(1)) if match else None


def document_id(document: dict[str, Any]) -> int | None:
    value = value_from_keys(
        document,
        "id",
        "document_id",
        "documentId",
    )
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return id_from_href(document.get("href"), "documents")


def document_type_id(document: dict[str, Any]) -> int | None:
    nested = (
        document.get("document_type")
        or document.get("documentType")
        or {}
    )

    if isinstance(nested, dict):
        if nested.get("id") is not None:
            try:
                return int(nested["id"])
            except (TypeError, ValueError):
                return None

        nested_id = id_from_href(
            nested.get("href"),
            "document_types",
        )
        if nested_id is not None:
            return nested_id

    value = value_from_keys(
        document,
        "document_type_id",
        "documentTypeId",
    )
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def document_total(document: dict[str, Any]) -> Decimal:
    return money(
        value_from_keys(
            document,
            "total_amount",
            "totalAmount",
            "total",
        )
    )


def payment_document_id(payment: dict[str, Any]) -> int | None:
    nested = payment.get("document") or {}

    if isinstance(nested, dict):
        if nested.get("id") is not None:
            try:
                return int(nested["id"])
            except (TypeError, ValueError):
                return None

        nested_id = id_from_href(
            nested.get("href"),
            "documents",
        )
        if nested_id is not None:
            return nested_id

    value = value_from_keys(
        payment,
        "document_id",
        "documentId",
    )
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def payment_type_id(payment: dict[str, Any]) -> int | None:
    nested = (
        payment.get("payment_type")
        or payment.get("paymentType")
        or {}
    )

    if isinstance(nested, dict):
        if nested.get("id") is not None:
            try:
                return int(nested["id"])
            except (TypeError, ValueError):
                return None

        nested_id = id_from_href(
            nested.get("href"),
            "payment_types",
        )
        if nested_id is not None:
            return nested_id

    value = value_from_keys(
        payment,
        "payment_type_id",
        "paymentTypeId",
    )
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def payment_state(payment: dict[str, Any]) -> int:
    value = payment.get("state", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def payment_amount(payment: dict[str, Any]) -> Decimal:
    return money(
        value_from_keys(
            payment,
            "amount",
            "paymentAmount",
            "totalAmount",
        )
    )


def document_sign(type_id: int | None) -> int:
    if type_id in DOCUMENT_TYPES["sale"]:
        return 1
    if type_id in DOCUMENT_TYPES["return"]:
        return -1
    if type_id in DOCUMENT_TYPES["adjustment"]:
        return 1
    return 0


def aggregate_documents(
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    sale_gross = Decimal("0.00")
    returns = Decimal("0.00")
    adjustments = Decimal("0.00")

    sale_documents = 0
    return_documents = 0
    adjustment_documents = 0

    for document in documents:
        type_id = document_type_id(document)
        total = document_total(document)

        if type_id in DOCUMENT_TYPES["sale"]:
            sale_gross += total
            sale_documents += 1
        elif type_id in DOCUMENT_TYPES["return"]:
            returns += total
            return_documents += 1
        elif type_id in DOCUMENT_TYPES["adjustment"]:
            adjustments += total
            adjustment_documents += 1

    sale_gross = money(sale_gross)
    returns = money(returns)
    adjustments = money(adjustments)
    net_sales = money(sale_gross - returns + adjustments)

    return {
        "venta_bruta": money_number(sale_gross),
        "devoluciones": money_number(returns),
        "ajustes": money_number(adjustments),
        "venta_neta": money_number(net_sales),
        "documentos_venta": sale_documents,
        "documentos_devolucion": return_documents,
        "documentos_ajuste": adjustment_documents,
    }


def index_payments_by_document(
    payments: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for payment in payments:
        linked_document_id = payment_document_id(payment)
        if linked_document_id is not None:
            index[str(linked_document_id)].append(payment)

    return dict(index)


def aggregate_payments(
    documents: list[dict[str, Any]],
    payments_by_document: dict[str, list[dict[str, Any]]],
) -> dict[str, float]:
    cash = Decimal("0.00")
    terminal = Decimal("0.00")
    flux = Decimal("0.00")
    other = Decimal("0.00")
    payments_total = Decimal("0.00")

    for document in documents:
        type_id = document_type_id(document)
        linked_document_id = document_id(document)

        if linked_document_id is None:
            continue

        sign = document_sign(type_id)
        if sign == 0:
            continue

        signed_document_total = money(
            document_total(document) * sign
        )
        linked_payments = payments_by_document.get(
            str(linked_document_id),
            [],
        )

        has_cash_payment = False
        document_terminal = Decimal("0.00")
        document_flux = Decimal("0.00")
        document_other = Decimal("0.00")

        for payment in linked_payments:
            if payment_state(payment) != ACTIVE_STATE:
                continue

            type_id_payment = payment_type_id(payment)
            signed_amount = money(
                payment_amount(payment) * sign
            )

            if type_id_payment in PAYMENT_TYPES["cash"]:
                has_cash_payment = True
            elif type_id_payment in PAYMENT_TYPES["terminal"]:
                document_terminal += signed_amount
            elif type_id_payment in PAYMENT_TYPES["flux"]:
                document_flux += signed_amount
            else:
                document_other += signed_amount

        document_terminal = money(document_terminal)
        document_flux = money(document_flux)
        document_other = money(document_other)

        document_cash = Decimal("0.00")
        if has_cash_payment:
            document_cash = money(
                signed_document_total
                - document_terminal
                - document_flux
                - document_other
            )

        cash += document_cash
        terminal += document_terminal
        flux += document_flux
        other += document_other
        payments_total += money(
            document_cash
            + document_terminal
            + document_flux
            + document_other
        )

    return {
        "efectivo": money_number(money(cash)),
        "terminal": money_number(money(terminal)),
        "flux": money_number(money(flux)),
        "otros_pagos": money_number(money(other)),
        "pagos_total": money_number(money(payments_total)),
    }


def build_daily_sale_id(
    target_date: date,
    office_id: int,
) -> str:
    return f"{target_date.isoformat()}_{office_id}"


def build_rows_for_date(
    client: BsaleClient,
    target_date: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    date_text = target_date.isoformat()
    emission_range = bsale_emission_date_range(target_date)
    record_date = bsale_record_date(target_date)

    LOGGER.info("Fetching payments for %s", date_text)
    all_payments = client.get_all_pages(
        PAYMENTS_ENDPOINT,
        {"recorddate": record_date},
    )
    payments_by_document = index_payments_by_document(
        all_payments
    )

    synced_at = datetime.now(
        ZoneInfo(TIMEZONE)
    ).strftime("%Y-%m-%d %H:%M:%S")

    rows: list[dict[str, Any]] = []
    office_metrics: list[dict[str, Any]] = []

    for office in (item for item in OFFICES if item.active):
        LOGGER.info(
            "Fetching documents for %s / office %s (%s)",
            date_text,
            office.office_id,
            office.branch,
        )

        documents = client.get_all_pages(
            DOCUMENTS_ENDPOINT,
            {
                "emissiondaterange": emission_range,
                "officeid": office.office_id,
                "state": ACTIVE_STATE,
            },
        )

        document_aggregate = aggregate_documents(documents)
        payment_aggregate = aggregate_payments(
            documents,
            payments_by_document,
        )

        payment_difference = money(
            Decimal(str(payment_aggregate["pagos_total"]))
            - Decimal(str(document_aggregate["venta_neta"]))
        )

        row = {
            "id_venta_diaria": build_daily_sale_id(
                target_date,
                office.office_id,
            ),
            "fecha": date_text,
            "periodo": target_date.strftime("%Y-%m"),
            "periodo_date": target_date.strftime("%Y-%m-01"),
            "office_id": office.office_id,
            "sucursal": office.branch,
            **document_aggregate,
            **payment_aggregate,
            "pagos_diferencia": money_number(
                payment_difference
            ),
            "synced_at": synced_at,
        }
        rows.append(row)

        office_metrics.append(
            {
                "office_id": office.office_id,
                "sucursal": office.branch,
                "documents_read": len(documents),
                "payment_difference": money_number(
                    payment_difference
                ),
            }
        )

    validation_warnings = [
        metric
        for metric in office_metrics
        if abs(metric["payment_difference"]) > 0.01
    ]

    metadata = {
        "date": date_text,
        "emissiondaterange": emission_range,
        "recorddate": record_date,
        "payments_read": len(all_payments),
        "offices": office_metrics,
        "validation": {
            "status": (
                "PASS"
                if not validation_warnings
                else "WARNING"
            ),
            "payment_difference_tolerance": 0.01,
            "offices_with_payment_difference": (
                validation_warnings
            ),
        },
    }

    return rows, metadata


def write_output(
    target_date: date,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (
        output_dir
        / f"ventas_diarias_{target_date.isoformat()}.json"
    )

    payload = {
        "status": "OK",
        **metadata,
        "rows_written": len(rows),
        "rows": rows,
    }

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract and aggregate daily Bsale sales and "
            "payments for Chino Regalado."
        )
    )
    parser.add_argument(
        "--date",
        help=(
            "Fiscal date in YYYY-MM-DD. Defaults to "
            "yesterday in Mexico City."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help=(
            "Directory where the JSON artifact will be "
            "written."
        ),
    )
    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    args = build_parser().parse_args()
    target_date = (
        parse_ymd(args.date)
        if args.date
        else yesterday_mexico_city()
    )

    LOGGER.info(
        "Starting daily extraction for %s",
        target_date.isoformat(),
    )

    client = BsaleClient(get_bsale_token())
    rows, metadata = build_rows_for_date(
        client,
        target_date,
    )
    output_path = write_output(
        target_date,
        rows,
        metadata,
        Path(args.output_dir),
    )

    summary = {
        "status": "OK",
        "date": target_date.isoformat(),
        "rows_written": len(rows),
        "payments_read": metadata["payments_read"],
        "documents_read": sum(
            office["documents_read"]
            for office in metadata["offices"]
        ),
        "validation": metadata["validation"]["status"],
        "output_file": str(output_path),
    }

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
