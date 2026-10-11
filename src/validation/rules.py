"""
src.validation.rules
~~~~~~~~~~~~~~~~~~~~
Concrete validator implementations.

Each validator has a single, named responsibility (SRP).
Adding a new business rule = add a new class here + register it in the chain.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from ..models.transaction import Transaction, ValidationStatus
from .base import BaseValidator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Required-fields validator
# ─────────────────────────────────────────────────────────────────────────────

class RequiredFieldsValidator(BaseValidator):
    """Rejects rows where mandatory columns are blank."""

    _REQUIRED = ("account_number", "txn_date_raw", "ref_no")

    @property
    def name(self) -> str:
        return "RequiredFieldsValidator"

    def validate(self, txn: Transaction) -> Transaction:
        for field_name in self._REQUIRED:
            val = str(getattr(txn, field_name, "") or "").strip()
            if not val:
                txn.add_rejection_reason(
                    f"MISSING_REQUIRED_FIELD: '{field_name}' is empty"
                )
        return txn


# ─────────────────────────────────────────────────────────────────────────────
# Date validator
# ─────────────────────────────────────────────────────────────────────────────

class DateValidator(BaseValidator):
    """
    Validates txn_date_raw is a real, parseable calendar date.

    Strategy:
    - Try each configured date format in order; first success wins.
    - After parsing, sanity-check the year is within [year_min, year_max].
    - On success: sets txn.txn_date to the normalised ISO date (YYYY-MM-DD).
    - On failure: adds INVALID_DATE rejection reason.
    """

    DEFAULT_FORMATS: List[str] = [
        "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"
    ]

    def __init__(
        self,
        date_formats: Optional[List[str]] = None,
        year_min: int = 2000,
        year_max: int = 2100,
    ) -> None:
        self._formats = date_formats or self.DEFAULT_FORMATS
        self._year_min = year_min
        self._year_max = year_max

    @property
    def name(self) -> str:
        return "DateValidator"

    def validate(self, txn: Transaction) -> Transaction:
        raw = txn.txn_date_raw
        if not raw:
            txn.add_rejection_reason("INVALID_DATE: txn_date is empty")
            return txn

        parsed = self._try_parse(raw)
        if parsed is None:
            txn.add_rejection_reason(
                f"INVALID_DATE: '{raw}' is not a valid calendar date"
            )
            logger.debug(
                "Invalid date '%s' in %s row %d",
                raw, txn.source_file, txn.row_number,
            )
        else:
            txn.txn_date = parsed.strftime("%Y-%m-%d")

        return txn

    def _try_parse(self, raw: str) -> Optional[datetime]:
        for fmt in self._formats:
            try:
                dt = datetime.strptime(raw.strip(), fmt)
                if self._year_min <= dt.year <= self._year_max:
                    return dt
                # Year out of bounds — keep trying other formats
            except ValueError:
                continue
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Amount validator
# ─────────────────────────────────────────────────────────────────────────────

class AmountValidator(BaseValidator):
    """
    Validates that exactly one of debit / credit is present and non-zero.

    Banking rule: a transaction is either a debit or a credit — never both,
    never neither (even a ₹0 fee is a debit/credit entry).
    """

    @property
    def name(self) -> str:
        return "AmountValidator"

    def validate(self, txn: Transaction) -> Transaction:
        has_debit = txn.debit is not None and txn.debit != 0.0
        has_credit = txn.credit is not None and txn.credit != 0.0

        if has_debit and has_credit:
            txn.add_rejection_reason(
                f"BOTH_DEBIT_AND_CREDIT: debit={txn.debit}, credit={txn.credit} "
                "— a transaction cannot be both a debit and a credit"
            )
        elif not has_debit and not has_credit:
            txn.add_rejection_reason(
                "NO_DEBIT_OR_CREDIT: both debit and credit are absent or zero"
            )

        return txn
