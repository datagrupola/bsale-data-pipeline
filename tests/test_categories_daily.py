import unittest

from src.build_categories_daily import (
    build_catalog_maps,
    build_categories_daily_summary,
    resolve_category,
    validate_categories_daily_summary,
)


def make_maps():
    catalogs = {
        "product_types": [
            {
                "product_type_id": 100,
                "product_type_name": "ACCESORIOS",
            }
        ],
        "products": [
            {
                "product_id": 200,
                "product_name": "Producto A",
                "product_type_id": 100,
            }
        ],
        "variants": [
            {
                "variant_id": 300,
                "product_id": 200,
            },
            {
                "variant_id": 301,
                "product_id": 999,
            },
        ],
    }

    return build_catalog_maps(catalogs)


class TestCategoriesDaily(unittest.TestCase):

    def test_resolve_complete_category(self):
        product_type_map, product_map, variant_map = (
            make_maps()
        )

        result = resolve_category(
            300,
            variant_map=variant_map,
            product_map=product_map,
            product_type_map=product_type_map,
        )

        self.assertEqual(result["variant_id"], 300)
        self.assertEqual(result["product_id"], 200)
        self.assertEqual(result["product_type_id"], 100)
        self.assertEqual(
            result["product_type_name"],
            "ACCESORIOS",
        )
        self.assertEqual(
            result["resolution_status"],
            "OK",
        )

    def test_missing_variant(self):
        product_type_map, product_map, variant_map = (
            make_maps()
        )

        result = resolve_category(
            9999,
            variant_map=variant_map,
            product_map=product_map,
            product_type_map=product_type_map,
        )

        self.assertEqual(result["variant_id"], 9999)
        self.assertIsNone(result["product_id"])
        self.assertIsNone(result["product_type_id"])
        self.assertIsNone(result["product_type_name"])
        self.assertEqual(
            result["resolution_status"],
            "MISSING_VARIANT",
        )

    def test_missing_product(self):
        product_type_map, product_map, variant_map = (
            make_maps()
        )

        result = resolve_category(
            301,
            variant_map=variant_map,
            product_map=product_map,
            product_type_map=product_type_map,
        )

        self.assertEqual(result["variant_id"], 301)
        self.assertEqual(result["product_id"], 999)
        self.assertIsNone(result["product_type_id"])
        self.assertIsNone(result["product_type_name"])
        self.assertEqual(
            result["resolution_status"],
            "MISSING_PRODUCT",
        )

    def test_missing_product_type_preserves_reference(self):
        catalogs = {
            "product_types": [],
            "products": [
                {
                    "product_id": 200,
                    "product_name": "Producto A",
                    "product_type_id": 999,
                }
            ],
            "variants": [
                {
                    "variant_id": 300,
                    "product_id": 200,
                }
            ],
        }

        product_type_map, product_map, variant_map = (
            build_catalog_maps(catalogs)
        )

        result = resolve_category(
            300,
            variant_map=variant_map,
            product_map=product_map,
            product_type_map=product_type_map,
        )

        self.assertEqual(result["variant_id"], 300)
        self.assertEqual(result["product_id"], 200)
        self.assertEqual(result["product_type_id"], 999)
        self.assertIsNone(result["product_type_name"])
        self.assertEqual(
            result["resolution_status"],
            "MISSING_PRODUCT_TYPE",
        )

    def test_build_categories_daily_summary(self):
        catalogs = {
            "product_types": [
                {
                    "product_type_id": 100,
                    "product_type_name": "ACCESORIOS",
                }
            ],
            "products": [
                {
                    "product_id": 200,
                    "product_type_id": 100,
                },
                {
                    "product_id": 201,
                    "product_type_id": 100,
                },
            ],
            "variants": [
                {
                    "variant_id": 300,
                    "product_id": 200,
                },
                {
                    "variant_id": 301,
                    "product_id": 201,
                },
            ],
        }

        product_rows = [
            {
                "fecha": "2026-07-27",
                "periodo": "2026-07",
                "office_id": 2,
                "sucursal": "AMERICAS",
                "variant_id": 300,
                "piezas": 2,
                "venta_total": 100.50,
                "net_amount": 86.64,
                "tax_amount": 13.86,
            },
            {
                "fecha": "2026-07-27",
                "periodo": "2026-07",
                "office_id": 2,
                "sucursal": "AMERICAS",
                "variant_id": 301,
                "piezas": 3,
                "venta_total": 200.25,
                "net_amount": 172.63,
                "tax_amount": 27.62,
            },
        ]

        result = build_categories_daily_summary(
            product_rows,
            catalogs,
            synced_at="2026-08-07 00:00:00",
        )

        self.assertEqual(len(result), 1)

        row = result[0]

        self.assertEqual(
            row["fecha"],
            "2026-07-27",
        )
        self.assertEqual(
            row["periodo"],
            "2026-07",
        )
        self.assertEqual(row["office_id"], 2)
        self.assertEqual(
            row["sucursal"],
            "AMERICAS",
        )
        self.assertEqual(
            row["product_type_id"],
            100,
        )
        self.assertEqual(
            row["product_type_name"],
            "ACCESORIOS",
        )
        self.assertEqual(
            row["resolution_status"],
            "OK",
        )
        self.assertEqual(row["piezas"], 5)
        self.assertEqual(
            row["venta_total"],
            300.75,
        )
        self.assertEqual(
            row["net_amount"],
            259.27,
        )
        self.assertEqual(
            row["tax_amount"],
            41.48,
        )
        self.assertEqual(
            row["source_variant_rows"],
            2,
        )

    def test_unresolved_variant_keeps_business_totals(self):
        catalogs = {
            "product_types": [],
            "products": [],
            "variants": [],
        }

        product_rows = [
            {
                "fecha": "2026-07-27",
                "periodo": "2026-07",
                "office_id": 3,
                "sucursal": "HUERTA",
                "variant_id": 9999,
                "piezas": 4,
                "venta_total": 450.25,
                "net_amount": 388.15,
                "tax_amount": 62.10,
            }
        ]

        result = build_categories_daily_summary(
            product_rows,
            catalogs,
            synced_at="2026-08-07 00:00:00",
        )

        self.assertEqual(len(result), 1)

        row = result[0]

        self.assertEqual(
            row["fecha"],
            "2026-07-27",
        )
        self.assertEqual(row["office_id"], 3)
        self.assertEqual(
            row["sucursal"],
            "HUERTA",
        )
        self.assertIsNone(
            row["product_type_id"]
        )
        self.assertIsNone(
            row["product_type_name"]
        )
        self.assertEqual(
            row["resolution_status"],
            "MISSING_VARIANT",
        )
        self.assertEqual(row["piezas"], 4)
        self.assertEqual(
            row["venta_total"],
            450.25,
        )
        self.assertEqual(
            row["net_amount"],
            388.15,
        )
        self.assertEqual(
            row["tax_amount"],
            62.10,
        )
        self.assertEqual(
            row["source_variant_rows"],
            1,
        )

    def test_validation_reconciles_exact_totals(self):
        product_rows = [
            {
                "fecha": "2026-07-27",
                "office_id": 2,
                "piezas": 5,
                "venta_total": 300.75,
                "net_amount": 259.27,
                "tax_amount": 41.48,
            }
        ]

        category_rows = [
            {
                "fecha": "2026-07-27",
                "office_id": 2,
                "piezas": 5,
                "venta_total": 300.75,
                "net_amount": 259.27,
                "tax_amount": 41.48,
                "resolution_status": "OK",
            }
        ]

        result = validate_categories_daily_summary(
            product_rows,
            category_rows,
        )

        self.assertEqual(
            result["status"],
            "OK",
        )
        self.assertEqual(
            result["partition_count"],
            1,
        )
        self.assertEqual(result["mismatch_count"], 0)
        self.assertEqual(result["unresolved_category_rows"], 0)
        self.assertEqual(result["partitions"][0]["status"], "OK")
        self.assertEqual(result["partitions"][0]["differences"]["piezas"], 0)
        self.assertEqual(result["partitions"][0]["differences"]["venta_total"], 0)

    def test_validation_includes_returns_amount(self):
        product_rows = [{
            "fecha": "2026-07-27", "office_id": 2, "piezas": 1,
            "venta_total": 100, "returns_amount": 20,
            "net_amount": 86.21, "tax_amount": 13.79,
        }]
        category_rows = [{
            "fecha": "2026-07-27", "office_id": 2, "piezas": 1,
            "venta_total": 100, "returns_amount": 0,
            "net_amount": 86.21, "tax_amount": 13.79,
            "resolution_status": "OK",
        }]
        result = validate_categories_daily_summary(product_rows, category_rows)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(
            result["partitions"][0]["differences"]["returns_amount"], -20
        )

    def test_validation_detects_mismatch_without_hiding_unresolved(self):
        product_rows = [
            {
                "fecha": "2026-07-27",
                "office_id": 3,
                "piezas": 4,
                "venta_total": 450.25,
                "net_amount": 388.15,
                "tax_amount": 62.10,
            }
        ]

        category_rows = [
            {
                "fecha": "2026-07-27",
                "office_id": 3,
                "piezas": 3,
                "venta_total": 400.25,
                "net_amount": 345.05,
                "tax_amount": 55.20,
                "resolution_status": "MISSING_VARIANT",
            }
        ]

        result = validate_categories_daily_summary(
            product_rows,
            category_rows,
        )

        self.assertEqual(
            result["status"],
            "ERROR",
        )
        self.assertEqual(
            result["partition_count"],
            1,
        )
        self.assertEqual(
            result["mismatch_count"],
            1,
        )
        self.assertEqual(
            result["unresolved_category_rows"],
            1,
        )
        self.assertEqual(
            result["partitions"][0]["status"],
            "MISMATCH",
        )
        self.assertEqual(
            result["partitions"][0]["differences"]["piezas"],
            -1,
        )
        self.assertEqual(
            result["partitions"][0]["differences"]["venta_total"],
            -50,
        )


if __name__ == "__main__":
    unittest.main()
