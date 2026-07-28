from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Office:
    office_id: int
    branch: str
    active: bool = True


BSALE_BASE_URL = os.getenv(
    "BSALE_BASE_URL",
    "https://api.bsale.com.mx/v1",
).rstrip("/")
BSALE_TOKEN_ENV = "BSALE_ACCESS_TOKEN"

DOCUMENTS_ENDPOINT = "/documents.json"
PAYMENTS_ENDPOINT = "/payments.json"

PAGE_LIMIT = int(os.getenv("BSALE_PAGE_LIMIT", "50"))
MAX_OFFSET = int(os.getenv("BSALE_MAX_OFFSET", "100000"))
ACTIVE_STATE = 0
TIMEZONE = "America/Mexico_City"

OFFICES = (
    Office(office_id=2, branch="AMERICAS"),
    Office(office_id=3, branch="HUERTA"),
    Office(office_id=4, branch="CENTRO_LEON"),
)

DOCUMENT_TYPES = {
    "sale": frozenset({10}),
    "return": frozenset({39}),
    "adjustment": frozenset({40}),
}

PAYMENT_TYPES = {
    "cash": frozenset({1}),
    "terminal": frozenset({2, 6, 16}),
    "flux": frozenset({17}),
}


def get_bsale_token() -> str:
    token = os.getenv(BSALE_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(
            f"Missing required environment variable: {BSALE_TOKEN_ENV}"
        )
    return token
