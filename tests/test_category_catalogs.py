from __future__ import annotations

import unittest
from typing import Any

from src.extract_category_catalogs import (
    fetch_category_catalogs,
    product_id_from_variant,
    product_type_id_from_product,
    validate_category_catalogs,
)


class FakeBsaleClient:
    def get_all_pages(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if endpoint == "/product_types.json":
            return [
                {
                    "id": 100,
                    "name": "PLAYERAS",
                    "state": 0,
                }
            ]

        if endpoint == "/products.json":
            return [
                {
                    "id": 200,
                    "name": "PLAYERA NEGRA",
                    "state": 0,
                    "product_type": {
                        "id": 100,
                    },
                }
            ]

        if endpoint == "/variants.json":
            return [
                {
                    "id": 300,
                    "code": "SKU-300",
                    "description": "PLAYERA NEGRA M",
                    "state": 0,
                    "product": {
                        "id": 200,
                    },
                }
            ]

        return []


class CategoryCatalogsTests(unittest.TestCase):
    def test_product_id_from_expanded_variant(self) -> None:
        variant = {
            "product": {
                "id": 1234,
            }
        }

        self.assertEqual(
            product_id_from_variant(variant),
            1234,
        )

    def test_product_id_from_href(self) -> None:
        variant = {
            "product": {
                "href": (
                    "https://api.bsale.com.mx/v1/"
                    "products/5678.json"
                )
            }
        }

        self.assertEqual(
            product_id_from_variant(variant),
            5678,
        )

    def test_product_type_id_from_product(self) -> None:
        product = {
            "product_type": {
                "id": 999,
            }
        }

        self.assertEqual(
            product_type_id_from_product(product),
            999,
        )

    def test_fetch_catalogs_uses_product_type_fallback(
        self,
    ) -> None:
        catalogs = fetch_category_catalogs(
            FakeBsaleClient()
        )

        self.assertEqual(
            len(catalogs["product_types"]),
            1,
        )

        self.assertEqual(
            len(catalogs["products"]),
            1,
        )

        self.assertEqual(
            len(catalogs["variants"]),
            1,
        )

        self.assertEqual(
            catalogs["products"][0][
                "product_type_name"
            ],
            "PLAYERAS",
        )

        self.assertEqual(
            catalogs["variants"][0]["product_id"],
            200,
        )

    def test_validation_detects_complete_catalog(
        self,
    ) -> None:
        catalogs = fetch_category_catalogs(
            FakeBsaleClient()
        )

        result = validate_category_catalogs(
            catalogs
        )

        self.assertEqual(
            result["status"],
            "OK",
        )

        self.assertEqual(
            result["review_count"],
            0,
        )

    def test_validation_detects_missing_product(
        self,
    ) -> None:
        catalogs = {
            "product_types": [],
            "products": [],
            "variants": [
                {
                    "variant_id": 300,
                    "variant_code": "SKU-300",
                    "variant_description": "PRUEBA",
                    "product_id": 999,
                    "state": 0,
                    "synced_at": "2026-08-07 12:00:00",
                }
            ],
        }

        result = validate_category_catalogs(
            catalogs
        )

        self.assertEqual(
            result["status"],
            "OK_WITH_REVIEW",
        )

        self.assertEqual(
            result[
                "variants_missing_product_count"
            ],
            1,
        )


if __name__ == "__main__":
    unittest.main()