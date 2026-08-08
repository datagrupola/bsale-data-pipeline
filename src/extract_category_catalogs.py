from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .bsale_client import BsaleClient
from .config import (
    PRODUCTS_ENDPOINT,
    PRODUCT_TYPES_ENDPOINT,
    VARIANTS_ENDPOINT,
    get_bsale_token,
)
from .db import get_db_connection


LOGGER = logging.getLogger(__name__)

CATALOG_NAMES = (
    "product_types",
    "products",
    "variants",
)

def to_int(value: Any) -> int:
    if value is None or value == "":
        return 0

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue

        text = str(value).strip()

        if text:
            return text

    return ""


def id_from_href(href: Any, resource: str) -> int:
    if not href:
        return 0

    match = re.search(
        rf"/{re.escape(resource)}/(\d+)\.json",
        str(href),
    )

    return to_int(match.group(1)) if match else 0


def product_id_from_variant(variant: dict[str, Any]) -> int:
    product = variant.get("product")

    if isinstance(product, dict):
        product_id = to_int(product.get("id"))

        if product_id:
            return product_id

    for key in ("productId", "product_id"):
        product_id = to_int(variant.get(key))

        if product_id:
            return product_id

    if isinstance(product, dict):
        return id_from_href(product.get("href"), "products")

    return 0


def product_type_id_from_product(product: dict[str, Any]) -> int:
    for relation_key in ("product_type", "productType"):
        relation = product.get(relation_key)

        if isinstance(relation, dict):
            product_type_id = to_int(relation.get("id"))

            if product_type_id:
                return product_type_id

    for key in ("productTypeId", "product_type_id"):
        product_type_id = to_int(product.get(key))

        if product_type_id:
            return product_type_id

    for relation_key in ("product_type", "productType"):
        relation = product.get(relation_key)

        if isinstance(relation, dict):
            product_type_id = id_from_href(
                relation.get("href"),
                "product_types",
            )

            if product_type_id:
                return product_type_id

    return 0


def product_type_name_from_product(
    product: dict[str, Any],
) -> str:
    for relation_key in ("product_type", "productType"):
        relation = product.get(relation_key)

        if isinstance(relation, dict):
            name = first_text(
                relation.get("name"),
                relation.get("description"),
            )

            if name:
                return name

    return ""

def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_product_type(
    item: dict[str, Any],
    synced_at: str,
) -> dict[str, Any]:
    return {
        "product_type_id": to_int(item.get("id")),
        "product_type_name": first_text(
            item.get("name"),
            item.get("description"),
            "N/A",
        ),
        "state": to_int(item.get("state")),
        "synced_at": synced_at,
    }


def normalize_product(
    item: dict[str, Any],
    synced_at: str,
) -> dict[str, Any]:
    return {
        "product_id": to_int(item.get("id")),
        "product_name": first_text(
            item.get("name"),
            item.get("description"),
        ),
        "product_type_id": product_type_id_from_product(item),
        "product_type_name": (
            product_type_name_from_product(item) or "N/A"
        ),
        "state": to_int(item.get("state")),
        "synced_at": synced_at,
    }


def normalize_variant(
    item: dict[str, Any],
    synced_at: str,
) -> dict[str, Any]:
    return {
        "variant_id": to_int(item.get("id")),
        "variant_code": first_text(
            item.get("code"),
            item.get("barcode"),
        ),
        "variant_description": first_text(
            item.get("description"),
            item.get("name"),
        ),
        "product_id": product_id_from_variant(item),
        "state": to_int(item.get("state")),
        "synced_at": synced_at,
    }

def is_meaningful_text(value: Any) -> bool:
    text = str(value or "").strip().upper()

    return text not in {
        "",
        "N/A",
        "NA",
        "NULL",
        "UNDEFINED",
    }


def fetch_category_catalogs(
    client: BsaleClient,
) -> dict[str, list[dict[str, Any]]]:
    synced_at = utc_now_text()

    product_type_items = client.get_all_pages(
        PRODUCT_TYPES_ENDPOINT,
    )

    product_types = [
        normalize_product_type(item, synced_at)
        for item in product_type_items
        if to_int(item.get("id"))
    ]

    product_type_map = {
        row["product_type_id"]: row
        for row in product_types
    }

    product_items = client.get_all_pages(
        PRODUCTS_ENDPOINT,
        {
            "expand": "[product_type]",
        },
    )

    products: list[dict[str, Any]] = []

    for item in product_items:
        if not to_int(item.get("id")):
            continue

        row = normalize_product(item, synced_at)

        if (
            not is_meaningful_text(row["product_type_name"])
            and row["product_type_id"]
        ):
            product_type = product_type_map.get(
                row["product_type_id"]
            )

            if product_type:
                row["product_type_name"] = product_type[
                    "product_type_name"
                ]

        products.append(row)

    variant_items = client.get_all_pages(
        VARIANTS_ENDPOINT,
        {
            "expand": "[product]",
        },
    )

    variants = [
        normalize_variant(item, synced_at)
        for item in variant_items
        if to_int(item.get("id"))
    ]

    return {
        "product_types": product_types,
        "products": products,
        "variants": variants,
    }

def validate_category_catalogs(
    catalogs: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    product_types = catalogs["product_types"]
    products = catalogs["products"]
    variants = catalogs["variants"]

    product_type_ids = {
        row["product_type_id"]
        for row in product_types
        if row["product_type_id"]
    }

    product_ids = {
        row["product_id"]
        for row in products
        if row["product_id"]
    }

    products_missing_product_type = [
        row
        for row in products
        if (
            not row["product_type_id"]
            or row["product_type_id"] not in product_type_ids
            or not is_meaningful_text(
                row["product_type_name"]
            )
        )
    ]

    variants_missing_product = [
        row
        for row in variants
        if (
            not row["product_id"]
            or row["product_id"] not in product_ids
        )
    ]

    review_count = (
        len(products_missing_product_type)
        + len(variants_missing_product)
    )

    return {
        "status": (
            "OK_WITH_REVIEW"
            if review_count
            else "OK"
        ),
        "product_types_count": len(product_types),
        "products_count": len(products),
        "variants_count": len(variants),
        "products_missing_product_type_count": (
            len(products_missing_product_type)
        ),
        "variants_missing_product_count": (
            len(variants_missing_product)
        ),
        "review_count": review_count,
        "products_missing_product_type_sample": (
            products_missing_product_type[:20]
        ),
        "variants_missing_product_sample": (
            variants_missing_product[:20]
        ),
    }

def build_payload(
    client: BsaleClient,
) -> dict[str, Any]:
    catalogs = fetch_category_catalogs(client)
    validation = validate_category_catalogs(catalogs)

    return {
        "status": validation["status"],
        "generated_at": utc_now_text(),
        "validation": validation,
        "product_types": catalogs["product_types"],
        "products": catalogs["products"],
        "variants": catalogs["variants"],
    }


def write_output(
    payload: dict[str, Any],
    *,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        output_dir
        / "category_catalogs.json"
    )

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return output_path


def persist_category_catalogs(payload: dict[str, Any]) -> dict[str, int]:
    """Upsert validated catalog entities without deleting historic entries.

    The tables are intentionally maintained independently from sales.  This
    avoids making a daily-sales partition depend on an API catalog refresh.
    """
    if payload.get("status") != "OK":
        raise RuntimeError(
            "No se actualizan los catálogos porque Bsale los reportó con "
            f"revisión pendiente. status={payload.get('status')}"
        )

    type_sql = """
        insert into public.category_product_types
            (product_type_id, product_type_name, is_active, synced_at)
        values (%(product_type_id)s, %(product_type_name)s,
                %(is_active)s, %(synced_at)s)
        on conflict (product_type_id) do update set
            product_type_name = excluded.product_type_name,
            is_active = excluded.is_active,
            synced_at = excluded.synced_at
    """
    product_sql = """
        insert into public.category_products
            (product_id, product_name, product_type_id, is_active, synced_at)
        values (%(product_id)s, %(product_name)s, %(product_type_id)s,
                %(is_active)s, %(synced_at)s)
        on conflict (product_id) do update set
            product_name = excluded.product_name,
            product_type_id = excluded.product_type_id,
            is_active = excluded.is_active,
            synced_at = excluded.synced_at
    """
    variant_sql = """
        insert into public.category_variants
            (variant_id, variant_code, variant_description, product_id,
             is_active, synced_at)
        values (%(variant_id)s, %(variant_code)s, %(variant_description)s,
                %(product_id)s, %(is_active)s, %(synced_at)s)
        on conflict (variant_id) do update set
            variant_code = excluded.variant_code,
            variant_description = excluded.variant_description,
            product_id = excluded.product_id,
            is_active = excluded.is_active,
            synced_at = excluded.synced_at
    """

    def record(row: dict[str, Any]) -> dict[str, Any]:
        return {**row, "is_active": to_int(row.get("state")) == 0}

    # ``cursor.execute()`` for every record turns a normal full catalog
    # refresh (tens of thousands of products/variants) into tens of
    # thousands of client/server round trips.  Use Psycopg's batched
    # execution instead: the complete catalog is still one transaction, but
    # it is sent to Neon in batches over the extended query protocol.
    records_by_catalog = {
        "product_types": [record(row) for row in payload["product_types"]],
        "products": [record(row) for row in payload["products"]],
        "variants": [record(row) for row in payload["variants"]],
    }
    statements = (
        ("product_types", type_sql),
        ("products", product_sql),
        ("variants", variant_sql),
    )

    counts = {name: 0 for name in CATALOG_NAMES}
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for catalog_name, statement in statements:
                records = records_by_catalog[catalog_name]
                LOGGER.info(
                    "Persisting %s category catalog rows in batch",
                    len(records),
                )
                if records:
                    cursor.executemany(statement, records)
                counts[catalog_name] = len(records)
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extrae los catálogos de tipos de producto, "
            "productos y variantes desde Bsale."
        )
    )

    parser.add_argument(
        "--output-dir",
        default="output",
        help=(
            "Directorio donde se escribirá "
            "el artefacto JSON."
        ),
    )

    parser.add_argument(
        "--write-db",
        action="store_true",
        help=(
            "Hace upsert de los tres catálogos en Neon. Sólo se permite "
            "cuando la validación del catálogo es OK."
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

    LOGGER.info(
        "Starting Bsale category catalogs extraction"
    )

    client = BsaleClient(
        get_bsale_token()
    )

    payload = build_payload(client)

    database_result = None
    if args.write_db:
        database_result = persist_category_catalogs(payload)
        LOGGER.info("Persisted category catalogs: %s", database_result)

    output_path = write_output(
        payload,
        output_dir=Path(args.output_dir),
    )

    validation = payload["validation"]

    execution_summary = {
        "status": payload["status"],
        "product_types_count": (
            validation["product_types_count"]
        ),
        "products_count": (
            validation["products_count"]
        ),
        "variants_count": (
            validation["variants_count"]
        ),
        "products_missing_product_type_count": (
            validation[
                "products_missing_product_type_count"
            ]
        ),
        "variants_missing_product_count": (
            validation[
                "variants_missing_product_count"
            ]
        ),
        "review_count": validation["review_count"],
        "output_file": str(output_path),
        "database": database_result,
    }

    print(
        json.dumps(
            execution_summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
