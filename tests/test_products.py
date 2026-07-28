from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from src.extract_products import (
    NormalizedDocument,
    build_rows_from_details,
    build_summary,
    extract_expanded_details,
    parse_ymd,
    quantity_sign,
    utc_unix_end,
    utc_unix_start,
)


class ProductsTests(unittest.TestCase):
    def test_parse_ymd_rejects_invalid_date(self) -> None:
        with self.assertRaises(ValueError):
            parse_ymd("2026-02-30")


    def test_bsale_utc_ranges_match_fiscal_day_logic(
        self,
    ) -> None:
        target = date(2026, 7, 27)

        expected_start = int(
            datetime(
                2026,
                7,
                27,
                0,
                0,
                0,
                tzinfo=timezone.utc,
            ).timestamp()
        )

        expected_end = int(
            datetime(
                2026,
                7,
                27,
                23,
                59,
                59,
                tzinfo=timezone.utc,
            ).timestamp()
        )

        self.assertEqual(
            utc_unix_start(target),
            expected_start,
        )

        self.assertEqual(
            utc_unix_end(target),
            expected_end,
        )


    def test_return_quantity_policy_preserves_rules(
        self,
    ) -> None:
        self.assertEqual(
            quantity_sign(10, "NET"),
            1,
        )

        self.assertEqual(
            quantity_sign(39, "NET"),
            -1,
        )

        self.assertEqual(
            quantity_sign(39, "GROSS"),
            1,
        )

        self.assertEqual(
            quantity_sign(39, "IGNORE"),
            0,
        )


    def test_expanded_details_detects_incomplete_payload(
        self,
    ) -> None:
        result = extract_expanded_details(
            {
                "count": 3,
                "items": [
                    {"id": 101},
                    {"id": 102},
                ],
            }
        )

        self.assertTrue(
            result.has_expanded_details
        )

        self.assertEqual(
            result.count,
            3,
        )

        self.assertEqual(
            len(result.details),
            2,
        )


    def test_return_document_subtracts_amounts_and_pieces(
        self,
    ) -> None:
        document = NormalizedDocument(
            document_id=5001,
            fecha="2026-07-27",
            periodo="2026-07",
            office_id=2,
            sucursal="AMERICAS",
            document_type_id=39,
            document_number="DEV-5001",
            state=0,
            total_amount=Decimal("20.00"),
            document_total_signed=Decimal("-20.00"),
            details_payload=None,
        )

        details = [
            {
                "id": 7001,
                "variant": {
                    "id": 9001,
                    "code": "SKU-001",
                    "description": "Producto prueba",
                },
                "quantity": 2,
                "totalAmount": 20,
                "netAmount": 17.24,
                "taxAmount": 2.76,
            }
        ]

        result = build_rows_from_details(
            document,
            details,
            used_fallback=False,
            synced_at="2026-07-28 12:00:00",
            return_quantity_policy="NET",
        )

        self.assertEqual(
            result.estado,
            "OK",
        )

        self.assertEqual(
            result.difference,
            Decimal("0"),
        )

        self.assertEqual(
            result.details_quantity,
            Decimal("-2"),
        )

        self.assertEqual(
            len(result.raw_rows),
            1,
        )

        row = result.raw_rows[0]

        self.assertEqual(
            row["quantity_signed"],
            -2,
        )

        self.assertEqual(
            row["total_amount_signed"],
            -20,
        )

        self.assertEqual(
            row["net_amount_signed"],
            -17.24,
        )

        self.assertEqual(
            row["tax_amount_signed"],
            -2.76,
        )


    def test_summary_consolidates_same_variant(
        self,
    ) -> None:
        raw_rows = [
            {
                "fecha": "2026-07-27",
                "periodo": "2026-07",
                "office_id": 4,
                "sucursal": "CENTRO_LEON",
                "variant_id": 9001,
                "variant_code": "SKU-001",
                "variant_description": "Producto prueba",
                "quantity_signed": 1,
                "total_amount_signed": 10,
                "net_amount_signed": 8.62,
                "tax_amount_signed": 1.38,
            },
            {
                "fecha": "2026-07-27",
                "periodo": "2026-07",
                "office_id": 4,
                "sucursal": "CENTRO_LEON",
                "variant_id": 9001,
                "variant_code": "SKU-001",
                "variant_description": "Producto prueba",
                "quantity_signed": 2,
                "total_amount_signed": 20,
                "net_amount_signed": 17.24,
                "tax_amount_signed": 2.76,
            },
        ]

        result = build_summary(
            raw_rows,
            synced_at="2026-07-28 12:00:00",
        )

        self.assertEqual(
            len(result),
            1,
        )

        summary = result[0]

        self.assertEqual(
            summary["piezas"],
            3,
        )

        self.assertEqual(
            summary["venta_total"],
            30,
        )

        self.assertEqual(
            summary["net_amount"],
            25.86,
        )

        self.assertEqual(
            summary["tax_amount"],
            4.14,
        )


if __name__ == "__main__":
    unittest.main()