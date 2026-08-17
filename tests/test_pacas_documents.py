from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from src.pacas.extract_documents import (
    PACAS_OFFICE_ID,
    extract_documents_for_date,
)


class FakeClient:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def get_all_pages(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        if endpoint == "/documents.json":
            return self.documents
        return []


def unix_date(value: str) -> int:
    return int(
        datetime.strptime(value, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


def make_document(document_id: int, type_id: int, total: float, seller_id: int = 67):
    return {
        "id": document_id,
        "emissionDate": unix_date("2026-08-15"),
        "number": document_id,
        "serialNumber": f"0000-{document_id}",
        "totalAmount": total,
        "netAmount": total / 1.16,
        "taxAmount": total - (total / 1.16),
        "document_type": {"id": str(type_id)},
        "office": {"id": str(PACAS_OFFICE_ID)},
        "user": {"id": str(seller_id)},
        "sellers": {
            "count": 1,
            "items": [
                {
                    "id": seller_id,
                    "firstName": "YAZMIN",
                    "lastName": "RIOS DE JESUS",
                }
            ],
        },
    }


class PacasDocumentsTests(unittest.TestCase):
    def test_extracts_sales_returns_and_sellers(self):
        client = FakeClient(
            [
                make_document(1, 45, 9900),
                make_document(2, 39, 3650),
            ]
        )

        documents, sellers, metadata = extract_documents_for_date(
            client,
            date(2026, 8, 15),
        )

        self.assertEqual(len(documents), 2)
        self.assertEqual(len(sellers), 2)
        self.assertEqual(documents[0]["movement_type"], "SALE")
        self.assertEqual(documents[1]["movement_type"], "RETURN")
        self.assertEqual(metadata["document_type_counts"], {"39": 1, "45": 1})
        self.assertEqual(metadata["totals"]["gross_sales"], 9900.0)
        self.assertEqual(metadata["totals"]["returns_amount"], 3650.0)
        self.assertEqual(metadata["totals"]["net_sales"], 6250.0)
        self.assertEqual(metadata["validation"]["status"], "PASS")

    def test_skips_unrelated_document_types(self):
        client = FakeClient(
            [
                make_document(1, 45, 9900),
                make_document(3, 40, 500),
            ]
        )

        documents, sellers, metadata = extract_documents_for_date(
            client,
            date(2026, 8, 15),
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(len(sellers), 1)
        self.assertEqual(metadata["validation"]["skipped_document_ids"], [3])

    def test_warns_when_document_has_multiple_sellers(self):
        document = make_document(1, 45, 9900)
        document["sellers"] = {
            "count": 2,
            "items": [
                {"id": 67, "firstName": "YAZMIN", "lastName": "RIOS"},
                {"id": 68, "firstName": "ALEXIS", "lastName": "LEON"},
            ],
        }
        client = FakeClient([document])

        _, sellers, metadata = extract_documents_for_date(
            client,
            date(2026, 8, 15),
        )

        self.assertEqual(len(sellers), 2)
        self.assertEqual(metadata["validation"]["status"], "WARNING")
        self.assertEqual(metadata["validation"]["multi_seller_documents"], [1])


if __name__ == "__main__":
    unittest.main()
