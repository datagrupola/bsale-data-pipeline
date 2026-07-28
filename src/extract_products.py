from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any, Iterable

from .bsale_client import BsaleApiError, BsaleClient
from .config import (
    ACTIVE_STATE,
    DOCUMENTS_ENDPOINT,
    DOCUMENT_TYPES,
    OFFICES,
    PAGE_LIMIT,
    get_bsale_token,
)


LOGGER = logging.getLogger("bsale.products")

ALLOWED_DOCUMENT_TYPE_IDS = frozenset().union(*DOCUMENT_TYPES.values())
NEGATIVE_AMOUNT_DOCUMENT_TYPE_IDS = DOCUMENT_TYPES["return"]

RETURN_QUANTITY_POLICY = "NET"
MAX_ALLOWED_DOCUMENT_DIFFERENCE = Decimal("1.00")
MAX_PAGES = 5000

@dataclass(frozen=True)
class NormalizedDocument:
    document_id: int
    fecha: str
    periodo: str
    office_id: int
    sucursal: str
    document_type_id: int
    document_number: str
    state: int
    total_amount: Decimal
    document_total_signed: Decimal
    details_payload: Any


@dataclass(frozen=True)
class ExpandedDetails:
    has_expanded_details: bool
    details: list[dict[str, Any]]
    count: int | None


@dataclass(frozen=True)
class ProcessedDocument:
    raw_rows: list[dict[str, Any]]
    validation_row: dict[str, Any]
    details_total: Decimal
    details_quantity: Decimal
    difference: Decimal
    details_count: int
    used_fallback: bool
    estado: str

def to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default

    try:
        return int(Decimal(str(value)))
    except Exception:
        return default


def js_round_decimal(value: Any, decimals: int = 2) -> Decimal:
    """
    Reproduce Math.round de JavaScript.

    En JavaScript, los valores exactamente a la mitad se redondean
    hacia infinito positivo, incluso cuando son negativos.
    """
    decimal_value = to_decimal(value)
    factor = Decimal(10) ** decimals
    scaled = decimal_value * factor

    rounded_integer = (
        scaled + Decimal("0.5")
    ).to_integral_value(rounding=ROUND_FLOOR)

    result = rounded_integer / factor

    if result == 0:
        return Decimal("0")

    return result


def number(value: Decimal) -> float | int:
    if value == value.to_integral_value():
        return int(value)

    return float(value)


def parse_ymd(value: str, field_name: str = "date") -> date:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f'Fecha inválida en {field_name}: "{value}". '
            "Formato esperado: YYYY-MM-DD."
        ) from exc

    if parsed.isoformat() != value:
        raise ValueError(
            f'Fecha inválida en {field_name}: "{value}". '
            "Formato esperado: YYYY-MM-DD."
        )

    return parsed


def ensure_valid_range(
    start_date: date,
    end_date: date,
) -> None:
    if start_date > end_date:
        raise ValueError(
            "Rango inválido: start_date no puede ser mayor "
            "que end_date. "
            f"{start_date.isoformat()} > {end_date.isoformat()}"
        )


def utc_unix_start(target_date: date) -> int:
    return int(
        datetime.combine(
            target_date,
            time.min,
            tzinfo=timezone.utc,
        ).timestamp()
    )


def utc_unix_end(target_date: date) -> int:
    return int(
        datetime.combine(
            target_date,
            time(23, 59, 59),
            tzinfo=timezone.utc,
        ).timestamp()
    )


def unix_to_utc_date_text(value: Any) -> str:
    unix_value = to_int(value)

    if not unix_value:
        return ""

    return datetime.fromtimestamp(
        unix_value,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d")

def first_non_empty(
    payload: dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]

    return None


def id_from_href(
    href: Any,
    resource_name: str | None = None,
) -> int:
    if not href:
        return 0

    if resource_name:
        pattern = rf"{re.escape(resource_name)}/(\d+)\.json$"
    else:
        pattern = r"/(\d+)\.json$"

    match = re.search(pattern, str(href))

    return int(match.group(1)) if match else 0


def nested_id(value: Any) -> int:
    if isinstance(value, bool):
        return 0

    if isinstance(value, (int, float, Decimal)):
        return to_int(value)

    if isinstance(value, dict):
        if value.get("id") is not None:
            return to_int(value.get("id"))

        return id_from_href(value.get("href"))

    return 0


def document_type_id(
    document: dict[str, Any],
) -> int:
    nested = (
        document.get("document_type")
        or document.get("documentType")
    )

    nested_value = nested_id(nested)

    if nested_value:
        return nested_value

    return to_int(
        first_non_empty(
            document,
            "document_type_id",
            "documentTypeId",
        )
    )


def amount_sign(document_type: int) -> int:
    if document_type in NEGATIVE_AMOUNT_DOCUMENT_TYPE_IDS:
        return -1

    return 1


def quantity_sign(
    document_type: int,
    return_quantity_policy: str = RETURN_QUANTITY_POLICY,
) -> int:
    if document_type not in DOCUMENT_TYPES["return"]:
        return 1

    policy = return_quantity_policy.upper().strip()

    if policy == "NET":
        return -1

    if policy == "GROSS":
        return 1

    if policy == "IGNORE":
        return 0

    raise ValueError(
        "return_quantity_policy inválida. "
        "Valores permitidos: NET, GROSS, IGNORE."
    )


def normalize_document(
    document: dict[str, Any],
    *,
    office_id: int,
    sucursal: str,
) -> NormalizedDocument:
    fecha = unix_to_utc_date_text(
        first_non_empty(
            document,
            "emissionDate",
            "emission_date",
        )
    )

    type_id = document_type_id(document)

    total_amount = to_decimal(
        first_non_empty(
            document,
            "totalAmount",
            "total_amount",
            "total",
        )
    )

    document_number = str(
        first_non_empty(
            document,
            "number",
            "serialNumber",
            "serial_number",
        )
        or ""
    )

    return NormalizedDocument(
        document_id=to_int(document.get("id")),
        fecha=fecha,
        periodo=fecha[:7] if fecha else "",
        office_id=office_id,
        sucursal=sucursal,
        document_type_id=type_id,
        document_number=document_number,
        state=to_int(document.get("state")),
        total_amount=total_amount,
        document_total_signed=(
            total_amount * amount_sign(type_id)
        ),
        details_payload=document.get("details"),
    )


def is_valid_document(
    document: NormalizedDocument,
) -> bool:
    return (
        document.document_id > 0
        and bool(document.fecha)
        and document.document_type_id
        in ALLOWED_DOCUMENT_TYPE_IDS
        and document.state == ACTIVE_STATE
    )

def extract_expanded_details(
    details_payload: Any,
) -> ExpandedDetails:
    if details_payload is None:
        return ExpandedDetails(
            has_expanded_details=False,
            details=[],
            count=None,
        )

    if isinstance(details_payload, list):
        details = [
            item
            for item in details_payload
            if isinstance(item, dict)
        ]

        return ExpandedDetails(
            has_expanded_details=True,
            details=details,
            count=len(details_payload),
        )

    if (
        isinstance(details_payload, dict)
        and isinstance(details_payload.get("items"), list)
    ):
        raw_items = details_payload["items"]

        details = [
            item
            for item in raw_items
            if isinstance(item, dict)
        ]

        count_value = details_payload.get("count")

        return ExpandedDetails(
            has_expanded_details=True,
            details=details,
            count=(
                to_int(count_value)
                if count_value is not None
                else None
            ),
        )

    return ExpandedDetails(
        has_expanded_details=False,
        details=[],
        count=None,
    )


def fetch_all_pages(
    client: BsaleClient,
    endpoint: str,
    params: dict[str, Any] | None = None,
    *,
    page_limit: int = PAGE_LIMIT,
    max_pages: int = MAX_PAGES,
) -> list[dict[str, Any]]:
    offset = 0
    page = 0
    all_items: list[dict[str, Any]] = []

    while True:
        page += 1

        if page > max_pages:
            raise BsaleApiError(
                f"Se alcanzó max_pages en {endpoint}. "
                f"offset={offset}, limit={page_limit}"
            )

        page_params = dict(params or {})

        page_params.update(
            {
                "limit": page_limit,
                "offset": offset,
            }
        )

        payload = client.get_json(
            endpoint,
            page_params,
        )

        items = payload.get("items", [])

        if items is None:
            items = []

        if not isinstance(items, list):
            raise BsaleApiError(
                "Se esperaba una lista en 'items'. "
                f"endpoint={endpoint}, offset={offset}"
            )

        if not items:
            break

        all_items.extend(
            item
            for item in items
            if isinstance(item, dict)
        )

        if len(items) < page_limit:
            break

        offset += page_limit

    return all_items


def fetch_documents(
    client: BsaleClient,
    *,
    start_date: date,
    end_date: date,
    office_id: int,
) -> list[dict[str, Any]]:
    emission_date_range = (
        f"[{utc_unix_start(start_date)},"
        f"{utc_unix_end(end_date)}]"
    )

    return fetch_all_pages(
        client,
        DOCUMENTS_ENDPOINT,
        {
            "officeid": office_id,
            "emissiondaterange": emission_date_range,
            "state": ACTIVE_STATE,
            "expand": "[details]",
        },
    )


def fetch_document_details(
    client: BsaleClient,
    document_id_value: int,
) -> list[dict[str, Any]]:
    return fetch_all_pages(
        client,
        (
            f"/documents/{document_id_value}"
            "/details.json"
        ),
    )

def normalize_detail(
    detail: dict[str, Any],
) -> dict[str, Any]:
    variant = detail.get("variant") or {}

    if not isinstance(variant, dict):
        variant = {}

    variant_id = nested_id(variant)

    if not variant_id:
        variant_id = to_int(
            first_non_empty(
                detail,
                "variantId",
                "variant_id",
            )
        )

    return {
        "detail_id": to_int(detail.get("id")),
        "variant_id": variant_id,
        "variant_code": str(
            first_non_empty(variant, "code")
            or first_non_empty(detail, "code")
            or ""
        ),
        "variant_description": str(
            first_non_empty(variant, "description")
            or first_non_empty(
                detail,
                "description",
                "name",
            )
            or ""
        ),
        "quantity": to_decimal(
            detail.get("quantity")
        ),
        "total_amount": to_decimal(
            first_non_empty(
                detail,
                "totalAmount",
                "total_amount",
                "total",
            )
        ),
        "net_amount": to_decimal(
            first_non_empty(
                detail,
                "netAmount",
                "net_amount",
            )
        ),
        "tax_amount": to_decimal(
            first_non_empty(
                detail,
                "taxAmount",
                "tax_amount",
            )
        ),
    }


def difference_is_acceptable(
    difference: Decimal,
    max_allowed_difference: Decimal = (
        MAX_ALLOWED_DOCUMENT_DIFFERENCE
    ),
) -> bool:
    rounded_difference = js_round_decimal(
        difference,
        2,
    )

    return (
        abs(rounded_difference)
        <= max_allowed_difference
    )


def build_rows_from_details(
    document: NormalizedDocument,
    details: Iterable[dict[str, Any]],
    *,
    used_fallback: bool,
    synced_at: str,
    return_quantity_policy: str = (
        RETURN_QUANTITY_POLICY
    ),
    max_allowed_difference: Decimal = (
        MAX_ALLOWED_DOCUMENT_DIFFERENCE
    ),
) -> ProcessedDocument:
    amount_multiplier = amount_sign(
        document.document_type_id
    )

    quantity_multiplier = quantity_sign(
        document.document_type_id,
        return_quantity_policy,
    )

    safe_details = [
        detail
        for detail in details
        if isinstance(detail, dict)
    ]

    raw_rows: list[dict[str, Any]] = []

    details_total = Decimal("0")
    details_quantity = Decimal("0")

    for detail in safe_details:
        normalized = normalize_detail(detail)

        quantity_signed = (
            normalized["quantity"]
            * quantity_multiplier
        )

        total_amount_signed = (
            normalized["total_amount"]
            * amount_multiplier
        )

        net_amount_signed = (
            normalized["net_amount"]
            * amount_multiplier
        )

        tax_amount_signed = (
            normalized["tax_amount"]
            * amount_multiplier
        )

        details_total += total_amount_signed
        details_quantity += quantity_signed

        raw_rows.append(
            {
                "fecha": document.fecha,
                "periodo": document.periodo,
                "office_id": document.office_id,
                "sucursal": document.sucursal,
                "document_type_id": (
                    document.document_type_id
                ),
                "document_id": document.document_id,
                "document_number": (
                    document.document_number
                ),
                "detail_id": normalized["detail_id"],
                "variant_id": normalized["variant_id"],
                "variant_code": (
                    normalized["variant_code"]
                ),
                "variant_description": (
                    normalized[
                        "variant_description"
                    ]
                ),
                "quantity_signed": number(
                    js_round_decimal(
                        quantity_signed,
                        4,
                    )
                ),
                "total_amount_signed": number(
                    js_round_decimal(
                        total_amount_signed,
                        2,
                    )
                ),
                "net_amount_signed": number(
                    js_round_decimal(
                        net_amount_signed,
                        2,
                    )
                ),
                "tax_amount_signed": number(
                    js_round_decimal(
                        tax_amount_signed,
                        2,
                    )
                ),
                "synced_at": synced_at,
            }
        )

    rounded_document_total = js_round_decimal(
        document.document_total_signed,
        2,
    )

    rounded_details_total = js_round_decimal(
        details_total,
        2,
    )

    difference = js_round_decimal(
        (
            rounded_details_total
            - rounded_document_total
        ),
        2,
    )

    rounded_quantity = js_round_decimal(
        details_quantity,
        4,
    )

    estado = (
        "OK"
        if difference_is_acceptable(
            difference,
            max_allowed_difference,
        )
        else "REVISAR"
    )

    validation_row = {
        "fecha": document.fecha,
        "periodo": document.periodo,
        "office_id": document.office_id,
        "sucursal": document.sucursal,
        "document_type_id": (
            document.document_type_id
        ),
        "document_id": document.document_id,
        "document_number": (
            document.document_number
        ),
        "document_total_signed": number(
            rounded_document_total
        ),
        "details_total_signed": number(
            rounded_details_total
        ),
        "diferencia": number(difference),
        "piezas_documento": number(
            rounded_quantity
        ),
        "details_count": len(safe_details),
        "details_source": (
            "FALLBACK"
            if used_fallback
            else "EXPANDED"
        ),
        "estado": estado,
    }

    return ProcessedDocument(
        raw_rows=raw_rows,
        validation_row=validation_row,
        details_total=rounded_details_total,
        details_quantity=rounded_quantity,
        difference=difference,
        details_count=len(safe_details),
        used_fallback=used_fallback,
        estado=estado,
    )

def get_expanded_details_or_fallback(
    client: BsaleClient,
    document: NormalizedDocument,
) -> tuple[list[dict[str, Any]], bool]:
    expanded = extract_expanded_details(
        document.details_payload
    )

    if expanded.has_expanded_details:
        details_are_incomplete = (
            expanded.count is not None
            and expanded.count > len(expanded.details)
        )

        if details_are_incomplete:
            details = fetch_document_details(
                client,
                document.document_id,
            )

            return details, True

        return expanded.details, False

    details = fetch_document_details(
        client,
        document.document_id,
    )

    return details, True


def process_document(
    client: BsaleClient,
    document: NormalizedDocument,
    *,
    synced_at: str,
    return_quantity_policy: str = (
        RETURN_QUANTITY_POLICY
    ),
    max_allowed_difference: Decimal = (
        MAX_ALLOWED_DOCUMENT_DIFFERENCE
    ),
) -> ProcessedDocument:
    details, used_fallback = (
        get_expanded_details_or_fallback(
            client,
            document,
        )
    )

    processed = build_rows_from_details(
        document,
        details,
        used_fallback=used_fallback,
        synced_at=synced_at,
        return_quantity_policy=(
            return_quantity_policy
        ),
        max_allowed_difference=(
            max_allowed_difference
        ),
    )

    expanded_details_do_not_match = (
        not processed.used_fallback
        and not difference_is_acceptable(
            processed.difference,
            max_allowed_difference,
        )
    )

    if expanded_details_do_not_match:
        fallback_details = fetch_document_details(
            client,
            document.document_id,
        )

        processed = build_rows_from_details(
            document,
            fallback_details,
            used_fallback=True,
            synced_at=synced_at,
            return_quantity_policy=(
                return_quantity_policy
            ),
            max_allowed_difference=(
                max_allowed_difference
            ),
        )

    return processed

def build_summary(
    raw_rows: Iterable[dict[str, Any]],
    *,
    synced_at: str,
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, int, str, int, str, str],
        dict[str, Any],
    ] = {}

    for row in raw_rows:
        key = (
            str(row["fecha"]),
            str(row["periodo"]),
            to_int(row["office_id"]),
            str(row["sucursal"]),
            to_int(row["variant_id"]),
            str(row["variant_code"]),
            str(row["variant_description"]),
        )

        if key not in grouped:
            grouped[key] = {
                "fecha": key[0],
                "periodo": key[1],
                "office_id": key[2],
                "sucursal": key[3],
                "variant_id": key[4],
                "variant_code": key[5],
                "variant_description": key[6],
                "piezas": Decimal("0"),
                "venta_total": Decimal("0"),
                "net_amount": Decimal("0"),
                "tax_amount": Decimal("0"),
                "synced_at": synced_at,
            }

        grouped[key]["piezas"] += to_decimal(
            row["quantity_signed"]
        )

        grouped[key]["venta_total"] += to_decimal(
            row["total_amount_signed"]
        )

        grouped[key]["net_amount"] += to_decimal(
            row["net_amount_signed"]
        )

        grouped[key]["tax_amount"] += to_decimal(
            row["tax_amount_signed"]
        )

    summary_rows: list[dict[str, Any]] = []

    for key in sorted(grouped):
        item = grouped[key]

        summary_rows.append(
            {
                "fecha": item["fecha"],
                "periodo": item["periodo"],
                "office_id": item["office_id"],
                "sucursal": item["sucursal"],
                "variant_id": item["variant_id"],
                "variant_code": item["variant_code"],
                "variant_description": (
                    item["variant_description"]
                ),
                "piezas": number(
                    js_round_decimal(
                        item["piezas"],
                        4,
                    )
                ),
                "venta_total": number(
                    js_round_decimal(
                        item["venta_total"],
                        2,
                    )
                ),
                "net_amount": number(
                    js_round_decimal(
                        item["net_amount"],
                        2,
                    )
                ),
                "tax_amount": number(
                    js_round_decimal(
                        item["tax_amount"],
                        2,
                    )
                ),
                "synced_at": item["synced_at"],
            }
        )

    return summary_rows

def resolve_offices(
    requested_office_ids: Iterable[int] | None,
) -> list[Any]:
    active_offices = [
        office
        for office in OFFICES
        if office.active
    ]

    if not requested_office_ids:
        return active_offices

    requested = {
        to_int(office_id)
        for office_id in requested_office_ids
    }

    selected = [
        office
        for office in active_offices
        if office.office_id in requested
    ]

    found = {
        office.office_id
        for office in selected
    }

    unknown = sorted(requested - found)

    if unknown:
        raise ValueError(
            "office_id no configurado: "
            + ", ".join(map(str, unknown))
        )

    if not selected:
        raise ValueError(
            "No hay sucursales válidas para procesar."
        )

    return selected


def target_partition_count(
    start_date: date,
    end_date: date,
    office_count: int,
) -> int:
    date_count = (
        end_date - start_date
    ).days + 1

    return date_count * office_count


def totals_by_office(
    summary_rows: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[
        str,
        dict[str, Decimal | int],
    ] = defaultdict(
        lambda: {
            "rows": 0,
            "piezas": Decimal("0"),
            "venta_total": Decimal("0"),
            "net_amount": Decimal("0"),
            "tax_amount": Decimal("0"),
            "gross_reconstruido": Decimal("0"),
        }
    )

    for row in summary_rows:
        key = (
            f'{row["office_id"]} - '
            f'{row["sucursal"]}'
        )

        item = result[key]

        item["rows"] = int(
            item["rows"]
        ) + 1

        item["piezas"] += to_decimal(
            row["piezas"]
        )

        item["venta_total"] += to_decimal(
            row["venta_total"]
        )

        item["net_amount"] += to_decimal(
            row["net_amount"]
        )

        item["tax_amount"] += to_decimal(
            row["tax_amount"]
        )

        item["gross_reconstruido"] += (
            to_decimal(row["net_amount"])
            + to_decimal(row["tax_amount"])
        )

    normalized: dict[
        str,
        dict[str, Any],
    ] = {}

    for key in sorted(result):
        item = result[key]

        normalized[key] = {
            "rows": int(item["rows"]),
            "piezas": number(
                js_round_decimal(
                    item["piezas"],
                    4,
                )
            ),
            "venta_total": number(
                js_round_decimal(
                    item["venta_total"],
                    2,
                )
            ),
            "net_amount": number(
                js_round_decimal(
                    item["net_amount"],
                    2,
                )
            ),
            "tax_amount": number(
                js_round_decimal(
                    item["tax_amount"],
                    2,
                )
            ),
            "gross_reconstruido": number(
                js_round_decimal(
                    item["gross_reconstruido"],
                    2,
                )
            ),
        }

    return normalized

def extract_products(
    client: BsaleClient,
    *,
    start_date: date,
    end_date: date,
    office_ids: Iterable[int] | None = None,
    max_documents: int | None = None,
    return_quantity_policy: str = (
        RETURN_QUANTITY_POLICY
    ),
    max_allowed_difference: Decimal = (
        MAX_ALLOWED_DOCUMENT_DIFFERENCE
    ),
) -> dict[str, Any]:
    ensure_valid_range(
        start_date,
        end_date,
    )

    offices = resolve_offices(
        office_ids
    )

    if (
        max_documents is not None
        and max_documents <= 0
    ):
        raise ValueError(
            "max_documents debe ser mayor que cero."
        )

    # Valida la política antes de consultar Bsale.
    quantity_sign(
        39,
        return_quantity_policy,
    )

    synced_at = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")

    raw_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    office_metrics: list[dict[str, Any]] = []

    total_documents_fetched = 0
    total_documents_used = 0
    total_details_fetched = 0
    total_fallback_details_fetches = 0

    for office in offices:
        LOGGER.info(
            (
                "Fetching documents: "
                "%s to %s / office %s (%s)"
            ),
            start_date.isoformat(),
            end_date.isoformat(),
            office.office_id,
            office.branch,
        )

        documents = fetch_documents(
            client,
            start_date=start_date,
            end_date=end_date,
            office_id=office.office_id,
        )

        total_documents_fetched += len(
            documents
        )

        usable_documents = [
            normalized
            for normalized in (
                normalize_document(
                    document,
                    office_id=office.office_id,
                    sucursal=office.branch,
                )
                for document in documents
            )
            if is_valid_document(normalized)
        ]

        if max_documents is not None:
            usable_documents = (
                usable_documents[:max_documents]
            )

        total_documents_used += len(
            usable_documents
        )

        office_details = 0
        office_fallbacks = 0
        office_reviews = 0

        for document in usable_documents:
            processed = process_document(
                client,
                document,
                synced_at=synced_at,
                return_quantity_policy=(
                    return_quantity_policy
                ),
                max_allowed_difference=(
                    max_allowed_difference
                ),
            )

            raw_rows.extend(
                processed.raw_rows
            )

            validation_rows.append(
                processed.validation_row
            )

            total_details_fetched += (
                processed.details_count
            )

            office_details += (
                processed.details_count
            )

            if processed.used_fallback:
                total_fallback_details_fetches += 1
                office_fallbacks += 1

            if processed.estado == "REVISAR":
                office_reviews += 1

        office_metrics.append(
            {
                "office_id": office.office_id,
                "sucursal": office.branch,
                "documents_fetched": len(
                    documents
                ),
                "documents_used": len(
                    usable_documents
                ),
                "details_fetched": (
                    office_details
                ),
                "fallback_details_fetches": (
                    office_fallbacks
                ),
                "validation_review_count": (
                    office_reviews
                ),
            }
        )

    summary_rows = build_summary(
        raw_rows,
        synced_at=synced_at,
    )

    review_count = sum(
        1
        for row in validation_rows
        if row["estado"] == "REVISAR"
    )

    validation_document_keys = [
        (
            row["office_id"],
            row["document_id"],
        )
        for row in validation_rows
    ]

    duplicate_validation_documents = (
        len(validation_document_keys)
        - len(set(validation_document_keys))
    )

    summary_keys = [
        (
            row["fecha"],
            row["office_id"],
            row["variant_id"],
            row["variant_code"],
            row["variant_description"],
        )
        for row in summary_rows
    ]

    duplicate_summary_keys = (
        len(summary_keys)
        - len(set(summary_keys))
    )

    status = (
        "OK_WITH_REVIEW"
        if review_count > 0
        else "OK"
    )

    if (
        duplicate_validation_documents > 0
        or duplicate_summary_keys > 0
    ):
        status = "ERROR"

    return {
        "status": status,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "offices": [
            {
                "office_id": office.office_id,
                "sucursal": office.branch,
            }
            for office in offices
        ],
        "target_partitions": (
            target_partition_count(
                start_date,
                end_date,
                len(offices),
            )
        ),
        "return_quantity_policy": (
            return_quantity_policy.upper()
        ),
        "max_allowed_document_difference": (
            number(max_allowed_difference)
        ),
        "documents_fetched": (
            total_documents_fetched
        ),
        "documents_used": (
            total_documents_used
        ),
        "details_fetched": (
            total_details_fetched
        ),
        "fallback_details_fetches": (
            total_fallback_details_fetches
        ),
        "raw_rows_count": len(raw_rows),
        "summary_rows_count": len(
            summary_rows
        ),
        "validation_rows_count": len(
            validation_rows
        ),
        "validation_review_count": (
            review_count
        ),
        "duplicate_validation_documents": (
            duplicate_validation_documents
        ),
        "duplicate_summary_keys": (
            duplicate_summary_keys
        ),
        "generated_at_utc": synced_at,
        "office_metrics": office_metrics,
        "totals_by_office": (
            totals_by_office(summary_rows)
        ),
        "summary_rows": summary_rows,
        "validation_rows": validation_rows,
    }

def output_filename(
    start_date: date,
    end_date: date,
    office_ids: Iterable[int] | None,
) -> str:
    if start_date == end_date:
        date_part = start_date.isoformat()
    else:
        date_part = (
            f"{start_date.isoformat()}"
            f"_a_{end_date.isoformat()}"
        )

    selected_offices = list(
        office_ids or []
    )

    if len(selected_offices) == 1:
        office_part = (
            f"_office_{selected_offices[0]}"
        )
    else:
        office_part = ""

    return (
        f"productos_{date_part}"
        f"{office_part}.json"
    )


def write_output(
    payload: dict[str, Any],
    *,
    output_dir: Path,
    filename: str,
) -> Path:
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = output_dir / filename

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
            "Extrae productos y piezas vendidos "
            "desde Bsale documents + details."
        )
    )

    parser.add_argument(
        "--start-date",
        required=True,
        help=(
            "Fecha inicial fiscal en formato "
            "YYYY-MM-DD."
        ),
    )

    parser.add_argument(
        "--end-date",
        help=(
            "Fecha final fiscal. Si se omite, "
            "usa start-date."
        ),
    )

    parser.add_argument(
        "--office-id",
        action="append",
        type=int,
        help=(
            "Sucursal a procesar. Puede repetirse. "
            "Si se omite, procesa 2, 3 y 4."
        ),
    )

    parser.add_argument(
        "--max-documents",
        type=int,
        help=(
            "Límite de documentos por sucursal "
            "para pruebas controladas."
        ),
    )

    parser.add_argument(
        "--return-quantity-policy",
        choices=(
            "NET",
            "GROSS",
            "IGNORE",
        ),
        default=RETURN_QUANTITY_POLICY,
        help=(
            "Política de piezas para "
            "devoluciones tipo 39."
        ),
    )

    parser.add_argument(
        "--output-dir",
        default="output",
        help=(
            "Directorio donde se escribirá "
            "el artefacto JSON."
        ),
    )

    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )

    args = build_parser().parse_args()

    start_date = parse_ymd(
        args.start_date,
        "start_date",
    )

    end_date = parse_ymd(
        args.end_date or args.start_date,
        "end_date",
    )

    ensure_valid_range(
        start_date,
        end_date,
    )

    LOGGER.info(
        "Starting products extraction: %s to %s",
        start_date.isoformat(),
        end_date.isoformat(),
    )

    client = BsaleClient(
        get_bsale_token()
    )

    payload = extract_products(
        client,
        start_date=start_date,
        end_date=end_date,
        office_ids=args.office_id,
        max_documents=args.max_documents,
        return_quantity_policy=(
            args.return_quantity_policy
        ),
    )

    filename = output_filename(
        start_date,
        end_date,
        args.office_id,
    )

    output_path = write_output(
        payload,
        output_dir=Path(args.output_dir),
        filename=filename,
    )

    execution_summary = {
        "status": payload["status"],
        "start_date": payload["start_date"],
        "end_date": payload["end_date"],
        "documents_fetched": (
            payload["documents_fetched"]
        ),
        "documents_used": (
            payload["documents_used"]
        ),
        "details_fetched": (
            payload["details_fetched"]
        ),
        "fallback_details_fetches": (
            payload[
                "fallback_details_fetches"
            ]
        ),
        "summary_rows_count": (
            payload["summary_rows_count"]
        ),
        "validation_review_count": (
            payload[
                "validation_review_count"
            ]
        ),
        "output_file": str(output_path),
    }

    print(
        json.dumps(
            execution_summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    if payload["status"] == "ERROR":
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())