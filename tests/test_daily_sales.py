from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from src.extract_daily_sales import (
    aggregate_documents,
    aggregate_payments,
    bsale_emission_date_range,
    bsale_record_date,
    index_payments_by_document,
    parse_ymd,
)


class DailySalesTests(unittest.TestCase):
    def test_parse_ymd_rejects_invalid_date(self) -> None:
        with self.assertRaises(ValueError):
            parse_ymd("2026-02-30")

    def test_bsale_utc_ranges_match_fiscal_day_logic(self) -> None:
        target = date(2026, 7, 27)
        start = int(
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
        end = int(
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
            bsale_emission_date_range(target),
            f"[{start},{end}]",
        )
        self.assertEqual(
            bsale_record_date(target),
            start,
        )

    def test_document_aggregation_preserves_business_rules(
        self,
    ) -> None:
        documents = [
            {
                "id": 1,
                "document_type": {"id": 10},
                "total_amount": 100,
            },
            {
                "id": 2,
                "document_type": {"id": 39},
                "total_amount": 20,
            },
            {
                "id": 3,
                "document_type": {"id": 40},
                "total_amount": 5,
            },
            {
                "id": 4,
                "document_type": {"id": 99},
                "total_amount": 999,
            },
        ]

        result = aggregate_documents(documents)

        self.assertEqual(result["venta_bruta"], 100.0)
        self.assertEqual(result["devoluciones"], 20.0)
        self.assertEqual(result["ajustes"], 5.0)
        self.assertEqual(result["venta_neta"], 85.0)
        self.assertEqual(result["documentos_venta"], 1)
        self.assertEqual(result["documentos_devolucion"], 1)
        self.assertEqual(result["documentos_ajuste"], 1)

    def test_cash_is_reconstructed_as_document_residual(
        self,
    ) -> None:
        documents = [
            {
                "id": 101,
                "document_type": {"id": 10},
                "total_amount": 100,
            },
            {
                "id": 102,
                "document_type": {"id": 39},
                "total_amount": 20,
            },
        ]
        payments = [
            {
                "document": {"id": 101},
                "payment_type": {"id": 1},
                "amount": 50,
                "state": 0,
            },
            {
                "document": {"id": 101},
                "payment_type": {"id": 2},
                "amount": 30,
                "state": 0,
            },
            {
                "document": {"id": 101},
                "payment_type": {"id": 17},
                "amount": 20,
                "state": 0,
            },
            {
                "document": {"id": 102},
                "payment_type": {"id": 1},
                "amount": 15,
                "state": 0,
            },
            {
                "document": {"id": 102},
                "payment_type": {"id": 2},
                "amount": 5,
                "state": 0,
            },
        ]

        result = aggregate_payments(
            documents,
            index_payments_by_document(payments),
        )

        self.assertEqual(result["efectivo"], 35.0)
        self.assertEqual(result["terminal"], 25.0)
        self.assertEqual(result["flux"], 20.0)
        self.assertEqual(result["otros_pagos"], 0.0)
        self.assertEqual(result["pagos_total"], 80.0)


if __name__ == "__main__":
    unittest.main()
