from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from ..db import get_db_connection
from .extract_documents import PACAS_OFFICE_ID, PACAS_OFFICE_NAME, parse_ymd, yesterday_mexico_city

LOGGER = logging.getLogger("bsale.pacas.daily")
MONEY_QUANTUM = Decimal("0.01")


def money(value: