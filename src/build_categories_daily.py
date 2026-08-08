from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row

from .extract_products import (
    ensure_valid_range,
    js_round_decimal,
    number,
    parse_ymd,
    to_decimal,
)
from .db import get_db_connection


def to_int(value: Any) -> int:
    if value is None or value == "":
        return 0

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def is_meaningful_text(value: Any) -> bool:
    text = str(value or "").strip().upper()

    return text not in {
        "",
        "N/A",
        "NA",
        "NULL",
        "UNDEFINED",
    }


def build_catalog_maps(
    catalogs: dict[str, list[dict[str, Any]]],
) -> tuple[
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    product_type_map = {
        to_int(row.get("product_type_id")): row
        for row in catalogs["product_types"]
        if to_int(row.get("product_type_id"))
    }

    product_map = {
        to_int(row.get("product_id")): row
        for row in catalogs["products"]
        if to_int(row.get("product_id"))
    }

    variant_map = {
        to_int(row.get("variant_id")): row
        for row in catalogs["variants"]
        if to_int(row.get("variant_id"))
    }

    return (
        product_type_map,
        product_map,
        variant_map,
    )


def resolve_category(
    variant_id: Any,
    *,
    variant_map: dict[int, dict[str, Any]],
    product_map: dict[int, dict[str, Any]],
    product_type_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    clean_variant_id = to_int(variant_id)
    variant = variant_map.get(clean_variant_id)

    if variant is None:
        return {
            "variant_id": clean_variant_id or None,
            "product_id": None,
            "product_type_id": None,
            "product_type_name": None,
            "resolution_status": "MISSING_VARIANT",
        }

    product_id = to_int(
        variant.get("product_id")
    )

    product = product_map.get(
        product_id
    )

    if product is None:
        return {
            "variant_id": clean_variant_id,
            "product_id": product_id or None,
            "product_type_id": None,
            "product_type_name": None,
            "resolution_status": "MISSING_PRODUCT",
        }

    product_type_id = to_int(
        product.get("product_type_id")
    )

    if not product_type_id:
        return {
            "variant_id": clean_variant_id,
            "product_id": product_id,
            "product_type_id": None,
            "product_type_name": None,
            "resolution_status": "MISSING_PRODUCT_TYPE",
        }

    product_type = product_type_map.get(
        product_type_id
    )

    if (
        product_type is None
        or not is_meaningful_text(
            product_type.get(
                "product_type_name"
            )
        )
    ):
        return {
            "variant_id": clean_variant_id,
            "product_id": product_id,
            "product_type_id": product_type_id,
            "product_type_name": None,
            "resolution_status": "MISSING_PRODUCT_TYPE",
        }

    return {
        "variant_id": clean_variant_id,
        "product_id": product_id,
        "product_type_id": product_type_id,
        "product_type_name": str(
            product_type[
                "product_type_name"
            ]
        ).strip(),
        "resolution_status": "OK",
    }


def build_categories_daily_summary(
    product_rows: list[dict[str, Any]],
    catalogs: dict[str, list[dict[str, Any]]],
    synced_at: str,
) -> list[dict[str, Any]]:
    (
        product_type_map,
        product_map,
        variant_map,
    ) = build_catalog_maps(
        catalogs
    )

    grouped: dict[
        tuple[
            str,
            str,
            int,
            str,
            int | None,
            str | None,
            str,
        ],
        dict[str, Any],
    ] = {}

    for product_row in product_rows:
        resolution = resolve_category(
            product_row.get("variant_id"),
            variant_map=variant_map,
            product_map=product_map,
            product_type_map=product_type_map,
        )

        fecha = str(
            product_row.get("fecha") or ""
        )

        periodo = str(
            product_row.get("periodo") or ""
        )

        office_id = to_int(
            product_row.get("office_id")
        )

        sucursal = str(
            product_row.get("sucursal") or ""
        ).strip()

        product_type_id = resolution[
            "product_type_id"
        ]

        product_type_name = resolution[
            "product_type_name"
        ]

        resolution_status = resolution[
            "resolution_status"
        ]

        key = (
            fecha,
            periodo,
            office_id,
            sucursal,
            product_type_id,
            product_type_name,
            resolution_status,
        )

        if key not in grouped:
            grouped[key] = {
                "fecha": fecha,
                "periodo": periodo,
                "office_id": office_id,
                "sucursal": sucursal,
                "product_type_id": product_type_id,
                "product_type_name": (
                    product_type_name
                ),
                "resolution_status": (
                    resolution_status
                ),
                "piezas": to_decimal(0),
                "venta_total": to_decimal(0),
                "returns_amount": to_decimal(0),
                "net_amount": to_decimal(0),
                "tax_amount": to_decimal(0),
                "source_variant_rows": 0,
            }

        aggregate = grouped[key]

        aggregate["piezas"] += to_decimal(
            product_row.get("piezas")
        )

        aggregate["venta_total"] += to_decimal(
            product_row.get("venta_total")
        )

        aggregate["returns_amount"] += to_decimal(
            product_row.get("returns_amount")
        )

        aggregate["net_amount"] += to_decimal(
            product_row.get("net_amount")
        )

        aggregate["tax_amount"] += to_decimal(
            product_row.get("tax_amount")
        )

        aggregate[
            "source_variant_rows"
        ] += 1

    category_rows: list[
        dict[str, Any]
    ] = []

    for aggregate in grouped.values():
        category_rows.append(
            {
                "fecha": aggregate[
                    "fecha"
                ],
                "periodo": aggregate[
                    "periodo"
                ],
                "office_id": aggregate[
                    "office_id"
                ],
                "sucursal": aggregate[
                    "sucursal"
                ],
                "product_type_id": aggregate[
                    "product_type_id"
                ],
                "product_type_name": (
                    aggregate[
                        "product_type_name"
                    ]
                ),
                "resolution_status": (
                    aggregate[
                        "resolution_status"
                    ]
                ),
                "piezas": number(
                    js_round_decimal(
                        aggregate["piezas"],
                        4,
                    )
                ),
                "venta_total": number(
                    js_round_decimal(
                        aggregate[
                            "venta_total"
                        ],
                        2,
                    )
                ),
                "returns_amount": number(
                    js_round_decimal(
                        aggregate["returns_amount"], 2
                    )
                ),
                "net_amount": number(
                    js_round_decimal(
                        aggregate[
                            "net_amount"
                        ],
                        2,
                    )
                ),
                "tax_amount": number(
                    js_round_decimal(
                        aggregate[
                            "tax_amount"
                        ],
                        2,
                    )
                ),
                "source_variant_rows": (
                    aggregate[
                        "source_variant_rows"
                    ]
                ),
                "synced_at": synced_at,
            }
        )

    category_rows.sort(
        key=lambda row: (
            str(row.get("fecha") or ""),
            to_int(row.get("office_id")),
            str(
                row.get(
                    "resolution_status"
                )
                or ""
            ),
            to_int(
                row.get(
                    "product_type_id"
                )
            ),
        )
    )

    return category_rows


def validate_categories_daily_summary(
    product_rows: list[dict[str, Any]],
    category_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    source_totals: dict[
        tuple[str, int],
        dict[str, Any],
    ] = defaultdict(
        lambda: {
            "piezas": to_decimal(0),
            "venta_total": to_decimal(0),
            "returns_amount": to_decimal(0),
            "net_amount": to_decimal(0),
            "tax_amount": to_decimal(0),
        }
    )

    category_totals: dict[
        tuple[str, int],
        dict[str, Any],
    ] = defaultdict(
        lambda: {
            "piezas": to_decimal(0),
            "venta_total": to_decimal(0),
            "returns_amount": to_decimal(0),
            "net_amount": to_decimal(0),
            "tax_amount": to_decimal(0),
        }
    )

    for row in product_rows:
        key = (
            str(row.get("fecha") or ""),
            to_int(row.get("office_id")),
        )

        source_totals[key][
            "piezas"
        ] += to_decimal(
            row.get("piezas")
        )

        source_totals[key][
            "venta_total"
        ] += to_decimal(
            row.get("venta_total")
        )
        source_totals[key]["returns_amount"] += to_decimal(
            row.get("returns_amount")
        )

        source_totals[key][
            "net_amount"
        ] += to_decimal(
            row.get("net_amount")
        )

        source_totals[key][
            "tax_amount"
        ] += to_decimal(
            row.get("tax_amount")
        )

    for row in category_rows:
        key = (
            str(row.get("fecha") or ""),
            to_int(row.get("office_id")),
        )

        category_totals[key][
            "piezas"
        ] += to_decimal(
            row.get("piezas")
        )

        category_totals[key][
            "venta_total"
        ] += to_decimal(
            row.get("venta_total")
        )
        category_totals[key]["returns_amount"] += to_decimal(
            row.get("returns_amount")
        )

        category_totals[key][
            "net_amount"
        ] += to_decimal(
            row.get("net_amount")
        )

        category_totals[key][
            "tax_amount"
        ] += to_decimal(
            row.get("tax_amount")
        )

    partition_keys = sorted(
        set(source_totals)
        | set(category_totals)
    )

    partitions: list[
        dict[str, Any]
    ] = []

    mismatch_count = 0

    for fecha, office_id in partition_keys:
        key = (
            fecha,
            office_id,
        )

        source = source_totals[key]
        target = category_totals[key]

        piezas_difference = (
            js_round_decimal(
                target["piezas"]
                - source["piezas"],
                4,
            )
        )

        venta_total_difference = (
            js_round_decimal(
                target["venta_total"]
                - source["venta_total"],
                2,
            )
        )
        returns_amount_difference = js_round_decimal(
            target["returns_amount"] - source["returns_amount"], 2
        )

        net_amount_difference = (
            js_round_decimal(
                target["net_amount"]
                - source["net_amount"],
                2,
            )
        )

        tax_amount_difference = (
            js_round_decimal(
                target["tax_amount"]
                - source["tax_amount"],
                2,
            )
        )

        differences = {
            "piezas": number(
                piezas_difference
            ),
            "venta_total": number(
                venta_total_difference
            ),
            "returns_amount": number(returns_amount_difference),
            "net_amount": number(
                net_amount_difference
            ),
            "tax_amount": number(
                tax_amount_difference
            ),
        }

        partition_status = (
            "OK"
            if all(
                difference == 0
                for difference
                in differences.values()
            )
            else "MISMATCH"
        )

        if (
            partition_status
            == "MISMATCH"
        ):
            mismatch_count += 1

        partitions.append(
            {
                "fecha": fecha,
                "office_id": office_id,
                "status": (
                    partition_status
                ),
                "differences": (
                    differences
                ),
            }
        )

    unresolved_category_rows = sum(
        1
        for row in category_rows
        if row.get(
            "resolution_status"
        )
        != "OK"
    )

    return {
        "status": (
            "OK"
            if mismatch_count == 0
            else "ERROR"
        ),
        "partition_count": len(
            partitions
        ),
        "mismatch_count": (
            mismatch_count
        ),
        "unresolved_category_rows": (
            unresolved_category_rows
        ),
        "partitions": partitions,
    }


def resolution_counts(
    category_rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for row in category_rows:
        status = str(
            row.get(
                "resolution_status"
            )
            or "UNKNOWN"
        )

        counts[status] = (
            counts.get(status, 0) + 1
        )

    return dict(
        sorted(
            counts.items()
        )
    )


def build_payload(
    products_payload: dict[str, Any],
    catalogs_payload: dict[str, Any],
) -> dict[str, Any]:
    product_rows = products_payload.get(
        "summary_rows"
    )

    if not isinstance(
        product_rows,
        list,
    ):
        raise ValueError(
            "products_payload.summary_rows "
            "debe ser una lista."
        )

    required_catalog_keys = (
        "product_types",
        "products",
        "variants",
    )

    for key in required_catalog_keys:
        if key not in catalogs_payload:
            raise ValueError(
                f"catalogs_payload.{key} es requerido."
            )

    product_types = catalogs_payload[
        "product_types"
    ]

    products = catalogs_payload[
        "products"
    ]

    variants = catalogs_payload[
        "variants"
    ]

    if not isinstance(
        product_types,
        list,
    ):
        raise ValueError(
            "catalogs_payload.product_types "
            "debe ser una lista."
        )

    if not isinstance(
        products,
        list,
    ):
        raise ValueError(
            "catalogs_payload.products "
            "debe ser una lista."
        )

    if not isinstance(
        variants,
        list,
    ):
        raise ValueError(
            "catalogs_payload.variants "
            "debe ser una lista."
        )

    catalogs = {
        "product_types": (
            product_types
        ),
        "products": products,
        "variants": variants,
    }

    synced_at = (
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    category_rows = (
        build_categories_daily_summary(
            product_rows,
            catalogs,
            synced_at,
        )
    )

    validation = (
        validate_categories_daily_summary(
            product_rows,
            category_rows,
        )
    )

    counts = resolution_counts(
        category_rows
    )

    unresolved_count = sum(
        count
        for resolution_status, count
        in counts.items()
        if resolution_status != "OK"
    )

    products_status = str(
        products_payload.get(
            "status"
        )
        or ""
    ).upper()

    catalogs_status = str(
        catalogs_payload.get(
            "status"
        )
        or ""
    ).upper()

    if (
        products_status == "ERROR"
        or catalogs_status == "ERROR"
        or validation["status"]
        == "ERROR"
    ):
        status = "ERROR"

    elif (
        products_status
        == "OK_WITH_REVIEW"
        or catalogs_status
        == "OK_WITH_REVIEW"
        or unresolved_count > 0
    ):
        status = "OK_WITH_REVIEW"

    else:
        status = "OK"

    return {
        "status": status,
        "start_date": (
            products_payload.get(
                "start_date"
            )
        ),
        "end_date": (
            products_payload.get(
                "end_date"
            )
        ),
        "generated_at_utc": (
            synced_at
        ),
        "source_products_status": (
            products_status
        ),
        "source_catalogs_status": (
            catalogs_status
        ),
        "source_product_rows_count": (
            len(product_rows)
        ),
        "category_rows_count": (
            len(category_rows)
        ),
        "resolution_counts": counts,
        "validation": validation,
        "summary_rows": (
            category_rows
        ),
    }


def load_json(
    path: Path,
) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            f"El JSON raíz debe ser "
            f"un objeto: {path}"
        )

    return payload


def output_filename(
    payload: dict[str, Any],
) -> str:
    start_date = str(
        payload.get(
            "start_date"
        )
        or ""
    ).strip()

    end_date = str(
        payload.get(
            "end_date"
        )
        or ""
    ).strip()

    if not start_date:
        return (
            "categorias_sin_fecha.json"
        )

    if (
        not end_date
        or start_date == end_date
    ):
        return (
            f"categorias_"
            f"{start_date}.json"
        )

    return (
        f"categorias_"
        f"{start_date}_"
        f"{end_date}.json"
    )


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
        / output_filename(
            payload
        )
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


def fetch_products_and_catalogs(
    *, start_date: str, end_date: str, office_ids: list[int] | None
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Read the already validated SKU grain and catalog relations from Neon."""
    office_filter = ""
    params: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
    if office_ids:
        office_filter = " and office_id = any(%(office_ids)s)"
        params["office_ids"] = office_ids
    products_sql = f"""
        select sale_date as fecha, to_char(sale_date, 'YYYY-MM') as periodo,
               office_id, office_name as sucursal, variant_id,
               variant_code, variant_description, pieces_sold as piezas,
               gross_sales as venta_total, returns_amount, net_sales as net_amount,
               tax_amount
        from public.products_daily
        where sale_date between %(start_date)s and %(end_date)s {office_filter}
        order by sale_date, office_id, variant_id
    """
    with get_db_connection() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(products_sql, params)
            product_rows = list(cursor.fetchall())
            cursor.execute("select product_type_id, product_type_name from public.category_product_types")
            product_types = list(cursor.fetchall())
            cursor.execute("select product_id, product_name, product_type_id from public.category_products")
            products = list(cursor.fetchall())
            cursor.execute("select variant_id, variant_code, variant_description, product_id from public.category_variants")
            variants = list(cursor.fetchall())
    for row in product_rows:
        row["fecha"] = str(row["fecha"])
    return product_rows, {"product_types": product_types, "products": products, "variants": variants}


def database_rows(category_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in category_rows:
        resolution_status = str(row["resolution_status"])
        product_type_id = row.get("product_type_id")
        if resolution_status == "OK":
            category_key = f"product_type:{product_type_id}"
            category_name = str(row["product_type_name"])
            db_status = "RESOLVED"
        else:
            category_key = f"{resolution_status}:unresolved"
            category_name = f"N/A ({resolution_status})"
            db_status = resolution_status
        records.append({
            "sale_date": row["fecha"], "office_id": row["office_id"],
            "office_name": row["sucursal"], "category_key": category_key,
            "category_name": category_name, "resolution_status": db_status,
            "pieces_sold": row["piezas"], "gross_sales": row["venta_total"],
            "returns_amount": row.get("returns_amount", 0),
            "net_sales": row["net_amount"], "tax_amount": row["tax_amount"],
        })
    return records


def persist_categories_daily(
    category_rows: list[dict[str, Any]],
    *,
    target_partitions: set[tuple[str, int]],
) -> dict[str, int]:
    records = database_rows(category_rows)
    delete_sql = "delete from public.categories_daily where sale_date = %(sale_date)s and office_id = %(office_id)s"
    insert_sql = """
        insert into public.categories_daily
        (sale_date, office_id, office_name, category_key, category_name,
         resolution_status, pieces_sold, gross_sales, returns_amount,
         net_sales, tax_amount, synced_at)
        values (%(sale_date)s, %(office_id)s, %(office_name)s, %(category_key)s,
                %(category_name)s, %(resolution_status)s, %(pieces_sold)s,
                %(gross_sales)s, %(returns_amount)s, %(net_sales)s,
                %(tax_amount)s, now())
    """
    deleted = inserted = 0
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            for sale_date, office_id in sorted(target_partitions):
                cursor.execute(delete_sql, {"sale_date": sale_date, "office_id": office_id})
                deleted += cursor.rowcount
            for record in records:
                cursor.execute(insert_sql, record)
                inserted += cursor.rowcount
    if inserted != len(records):
        raise RuntimeError(f"categories_daily insertó {inserted} de {len(records)} filas.")
    return {"target_rows": len(records), "deleted_rows": deleted, "inserted_rows": inserted}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Construye el resumen diario "
            "de categorías a partir del "
            "resumen de Products y los "
            "catálogos de categorías."
        )
    )

    parser.add_argument(
        "--products-file",
        help=(
            "JSON generado por "
            "src.extract_products."
        ),
    )

    parser.add_argument(
        "--catalogs-file",
        help=(
            "JSON generado por "
            "src.extract_category_catalogs."
        ),
    )

    parser.add_argument(
        "--output-dir",
        default="output",
        help=(
            "Directorio donde se "
            "escribirá el artefacto JSON."
        ),
    )

    parser.add_argument("--start-date", help="Fecha fiscal inicial YYYY-MM-DD para leer Neon.")
    parser.add_argument("--end-date", help="Fecha fiscal final; por defecto start-date.")
    parser.add_argument("--office-id", action="append", type=int,
                        help="Sucursal a materializar; puede repetirse.")
    parser.add_argument("--write-db", action="store_true",
                        help="Lee products_daily y catálogos desde Neon y reemplaza categorías por partición validada.")

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.write_db:
        if not args.start_date:
            raise ValueError("--start-date es requerido con --write-db.")
        start = parse_ymd(args.start_date, "start_date")
        end = parse_ymd(args.end_date or args.start_date, "end_date")
        ensure_valid_range(start, end)
        rows, catalogs = fetch_products_and_catalogs(
            start_date=start.isoformat(), end_date=end.isoformat(), office_ids=args.office_id
        )
        products_payload = {
            "status": "OK", "start_date": start.isoformat(),
            "end_date": end.isoformat(), "summary_rows": rows,
        }
        catalogs_payload = {"status": "OK", **catalogs}
    else:
        if not args.products_file or not args.catalogs_file:
            raise ValueError("--products-file y --catalogs-file son requeridos sin --write-db.")
        products_payload = load_json(Path(args.products_file))
        catalogs_payload = load_json(Path(args.catalogs_file))

    payload = build_payload(
        products_payload,
        catalogs_payload,
    )

    database_result = None
    if args.write_db:
        if payload["validation"]["status"] != "OK":
            raise RuntimeError("No se escribió categories_daily: el cuadre no es exacto.")
        office_ids = args.office_id or [2, 3, 4]
        current_date = start
        target_partitions: set[tuple[str, int]] = set()
        while current_date <= end:
            target_partitions.update(
                (current_date.isoformat(), office_id) for office_id in office_ids
            )
            current_date += timedelta(days=1)
        database_result = persist_categories_daily(
            payload["summary_rows"], target_partitions=target_partitions
        )

    output_path = write_output(
        payload,
        output_dir=Path(
            args.output_dir
        ),
    )

    validation = payload[
        "validation"
    ]

    execution_summary = {
        "status": payload[
            "status"
        ],
        "source_product_rows_count": (
            payload[
                "source_product_rows_count"
            ]
        ),
        "category_rows_count": (
            payload[
                "category_rows_count"
            ]
        ),
        "resolution_counts": (
            payload[
                "resolution_counts"
            ]
        ),
        "reconciliation_status": (
            validation[
                "status"
            ]
        ),
        "mismatch_count": (
            validation[
                "mismatch_count"
            ]
        ),
        "output_file": str(
            output_path
        ),
        "database": database_result,
    }

    print(
        json.dumps(
            execution_summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    return (
        1
        if payload["status"]
        == "ERROR"
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
