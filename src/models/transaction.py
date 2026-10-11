"""
src.models.transaction
~~~~~~~~~~~~~~~~~~~~~~
Canonical data model for a bank transaction.

Design decisions:
- Uses a dataclass (not dict) so IDE type-checking and attribute access are safe.
- record_hash is computed from *content* fields only (not source metadata) so
  the same real transaction has the same hash regardless of which file it came from.
- ValidationStatus uses str-mixin so it serialises cleanly to CSV/JSON.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ValidationStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"


@dataclass
class Transaction:
    """Canonical representation of a single bank transaction row."""

    # ── Source fields (raw as read from CSV) ───────────────────────────────
    account_number: str
    txn_date_raw: str       # Original date string, exactly as in the file
    txn_time: str
    description: str
    ref_no: str
    debit: Optional[float]
    credit: Optional[float]
    balance: Optional[float]
    account_type: str

    # ── Pipeline metadata ──────────────────────────────────────────────────
    source_file: str = field(default="")
    row_number: int = field(default=0)

    # ── Computed / enriched by pipeline ────────────────────────────────────
    txn_date: Optional[str] = field(default=None)   # Normalised YYYY-MM-DD
    record_hash: str = field(default="")

    # ── Validation state ───────────────────────────────────────────────────
    validation_status: ValidationStatus = field(default=ValidationStatus.VALID)
    rejection_reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.record_hash = self._compute_hash()

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    def _compute_hash(self) -> str:
        """
        SHA-256 of the content fields (excluding pipeline metadata).
        Two rows with identical business content → same hash.
        NB: uses txn_date_raw so the hash is stable against re-parsing.
        """
        parts = [
            str(self.account_number or ""),
            str(self.txn_date_raw or ""),
            str(self.txn_time or ""),
            str(self.description or ""),
            str(self.ref_no or ""),
            str(self.debit if self.debit is not None else ""),
            str(self.credit if self.credit is not None else ""),
            str(self.balance if self.balance is not None else ""),
            str(self.account_type or ""),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Mutation helpers (return self for fluent usage)
    # ------------------------------------------------------------------

    def add_rejection_reason(self, reason: str) -> "Transaction":
        if reason not in self.rejection_reasons:
            self.rejection_reasons.append(reason)
        self.validation_status = ValidationStatus.INVALID
        return self

    def mark_as_duplicate(self, reason: str) -> "Transaction":
        if reason not in self.rejection_reasons:
            self.rejection_reasons.append(reason)
        self.validation_status = ValidationStatus.DUPLICATE
        return self

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Canonical output row (valid records)."""
        return {
            "account_number": self.account_number,
            "txn_date": self.txn_date or self.txn_date_raw,
            "txn_time": self.txn_time,
            "description": self.description,
            "ref_no": self.ref_no,
            "debit": self.debit,
            "credit": self.credit,
            "balance": self.balance,
            "account_type": self.account_type,
            "source_file": self.source_file,
        }

    def to_quarantine_dict(self) -> dict:
        """Quarantine output row (invalid + duplicate records)."""
        return {
            **self.to_dict(),
            "txn_date_raw": self.txn_date_raw,
            "validation_status": self.validation_status.value,
            "rejection_reasons": "; ".join(self.rejection_reasons),
            "row_number": self.row_number,
            "record_hash": self.record_hash,
        }
