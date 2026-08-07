import unittest

from src.build_categories_daily import build_payload


class CategoriesPayloadTests(unittest.TestCase):
    def test_build_payload_reconciles_sources(self):
        products_payload = {
            "status": "OK",
            "start_date": "2026-07-25",
            "end_date": "2026-07-25",
            "summary_rows": [
                {
                    "fecha": "2026-07-25",
                    "periodo": "2026-07",
                    "office_id": 2,
                    "sucursal": "AMERICAS",
                    "variant_id": 1001,
                    "variant_code": "SKU-1001",
                    "variant_description": "Producto 1",
                    "piezas": 2,
                    "venta_total": 120.50,
                    "net_amount": 103.88,
                    "tax_amount": 16.62,
                },
                {
                    "fecha": "2026-07-25",
                    "periodo": "2026-07",
                    "office_id": 2,
                    "sucursal": "AMERICAS",
                    "variant_id": 1002,
                    "variant_code": "SKU-1002",
                    "variant_description": "Producto 2",
                    "piezas": 3,
                    "venta_total": 180.25,
                    "net_amount": 155.39,
                    "tax_amount": 24.86,
                },
            ],
        }

        catalogs_payload = {
            "status": "OK",
            "product_types": [
                {
                    "product_type_id": 100,
                    "product_type_name": "HOGAR",
                }
            ],
            "products": [
                {
                    "product_id": 501,
                    "product_type_id": 100,
                },
                {
                    "product_id": 502,
                    "product_type_id": 100,
                },
            ],
            "variants": [
                {
                    "variant_id": 1001,
                    "product_id": 501,
                },
                {
                    "variant_id": 1002,
                    "product_id": 502,
                },
            ],
        }

        payload = build_payload(
            products_payload,
            catalogs_payload,
        )

        self.assertEqual(
            payload["status"],
            "OK",
        )
        self.assertEqual(
            payload["source_products_status"],
            "OK",
        )
        self.assertEqual(
            payload["source_catalogs_status"],
            "OK",
        )
        self.assertEqual(
            payload["source_product_rows_count"],
            2,
        )
        self.assertEqual(
            payload["category_rows_count"],
            1,
        )

        self.assertEqual(
            payload["resolution_counts"],
            {
                "OK": 1,
            },
        )

        self.assertEqual(
            payload["validation"]["status"],
            "OK",
        )
        self.assertEqual(
            payload["validation"]["mismatch_count"],
            0,
        )
        self.assertEqual(
            payload["validation"][
                "unresolved_category_rows"
            ],
            0,
        )

        self.assertEqual(
            len(payload["summary_rows"]),
            1,
        )

        row = payload["summary_rows"][0]

        self.assertEqual(
            row["fecha"],
            "2026-07-25",
        )
        self.assertEqual(
            row["periodo"],
            "2026-07",
        )
        self.assertEqual(
            row["office_id"],
            2,
        )
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
            "HOGAR",
        )
        self.assertEqual(
            row["resolution_status"],
            "OK",
        )
        self.assertEqual(
            row["piezas"],
            5,
        )
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

    def test_build_payload_marks_unresolved_without_losing_totals(self):
        products_payload = {
            "status": "OK",
            "start_date": "2026-07-25",
            "end_date": "2026-07-25",
            "summary_rows": [
                {
                    "fecha": "2026-07-25",
                    "periodo": "2026-07",
                    "office_id": 3,
                    "sucursal": "HUERTA",
                    "variant_id": 9999,
                    "variant_code": "SKU-9999",
                    "variant_description": (
                        "Producto sin catálogo"
                    ),
                    "piezas": 4,
                    "venta_total": 250.00,
                    "net_amount": 215.52,
                    "tax_amount": 34.48,
                }
            ],
        }

        catalogs_payload = {
            "status": "OK",
            "product_types": [],
            "products": [],
            "variants": [],
        }

        payload = build_payload(
            products_payload,
            catalogs_payload,
        )

        self.assertEqual(
            payload["status"],
            "OK_WITH_REVIEW",
        )
        self.assertEqual(
            payload["source_product_rows_count"],
            1,
        )
        self.assertEqual(
            payload["category_rows_count"],
            1,
        )

        self.assertEqual(
            payload["resolution_counts"],
            {
                "MISSING_VARIANT": 1,
            },
        )

        self.assertEqual(
            payload["validation"]["status"],
            "OK",
        )
        self.assertEqual(
            payload["validation"]["mismatch_count"],
            0,
        )
        self.assertEqual(
            payload["validation"][
                "unresolved_category_rows"
            ],
            1,
        )

        row = payload["summary_rows"][0]

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

        self.assertEqual(
            row["piezas"],
            4,
        )
        self.assertEqual(
            row["venta_total"],
            250.00,
        )
        self.assertEqual(
            row["net_amount"],
            215.52,
        )
        self.assertEqual(
            row["tax_amount"],
            34.48,
        )

    def test_build_payload_propagates_products_error(self):
        products_payload = {
            "status": "ERROR",
            "start_date": "2026-07-25",
            "end_date": "2026-07-25",
            "summary_rows": [],
        }

        catalogs_payload = {
            "status": "OK",
            "product_types": [],
            "products": [],
            "variants": [],
        }

        payload = build_payload(
            products_payload,
            catalogs_payload,
        )

        self.assertEqual(
            payload["status"],
            "ERROR",
        )
        self.assertEqual(
            payload["source_products_status"],
            "ERROR",
        )
        self.assertEqual(
            payload["source_catalogs_status"],
            "OK",
        )

    def test_build_payload_propagates_catalogs_error(self):
        products_payload = {
            "status": "OK",
            "start_date": "2026-07-25",
            "end_date": "2026-07-25",
            "summary_rows": [],
        }

        catalogs_payload = {
            "status": "ERROR",
            "product_types": [],
            "products": [],
            "variants": [],
        }

        payload = build_payload(
            products_payload,
            catalogs_payload,
        )

        self.assertEqual(
            payload["status"],
            "ERROR",
        )
        self.assertEqual(
            payload["source_products_status"],
            "OK",
        )
        self.assertEqual(
            payload["source_catalogs_status"],
            "ERROR",
        )

    def test_build_payload_requires_all_catalog_lists(self):
        products_payload = {
            "status": "OK",
            "start_date": "2026-07-25",
            "end_date": "2026-07-25",
            "summary_rows": [],
        }

        complete_catalogs = {
            "status": "OK",
            "product_types": [],
            "products": [],
            "variants": [],
        }

        for missing_key in (
            "product_types",
            "products",
            "variants",
        ):
            catalogs_payload = dict(
                complete_catalogs
            )

            catalogs_payload.pop(
                missing_key
            )

            with self.subTest(
                missing_key=missing_key
            ):
                with self.assertRaises(
                    ValueError
                ):
                    build_payload(
                        products_payload,
                        catalogs_payload,
                    )

    def test_build_payload_propagates_source_review_status(self):
        products_payload = {
            "status": "OK_WITH_REVIEW",
            "start_date": "2026-07-25",
            "end_date": "2026-07-25",
            "summary_rows": [],
        }

        catalogs_payload = {
            "status": "OK",
            "product_types": [],
            "products": [],
            "variants": [],
        }

        payload = build_payload(
            products_payload,
            catalogs_payload,
        )

        self.assertEqual(
            payload["status"],
            "OK_WITH_REVIEW",
        )
        self.assertEqual(
            payload["source_products_status"],
            "OK_WITH_REVIEW",
        )
        self.assertEqual(
            payload["source_catalogs_status"],
            "OK",
        )


if __name__ == "__main__":
    unittest.main()