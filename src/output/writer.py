"""
src.output.writer
~~~~~~~~~~~~~~~~~
Writes canonical and quarantine CSVs to disk.

Design notes:
- Atomic-style write: writes to a temp file then renames, so a partial
  failure never leaves a corrupt output file.
- Column ordering is deterministic (defined by constant lists).
- If the output file already exists it is overwritten — the pipeline
  always produces a complete, fresh snapshot.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import List

import pandas as pd

from ..models.transaction import Transaction

logger = logging.getLogger(__name__)

# Column order for canonical output
CANONICAL_COLUMNS = [
    "account_number",
    "txn_date",
    "txn_time",
    "description",
    "ref_no",
    "debit",
    "credit",
    "balance",
    "account_type",
    "source_file",
]

# Additional metadata appended for quarantine output
QUARANTINE_EXTRA_COLUMNS = [
    "txn_date_raw",
    "validation_status",
    "rejection_reasons",
    "row_number",
    "record_hash",
]

QUARANTINE_COLUMNS = CANONICAL_COLUMNS + QUARANTINE_EXTRA_COLUMNS


class TransactionWriter:
    """Writes valid (canonical) and invalid/duplicate (quarantine) records to CSV."""

    def __init__(self, output_dir: Path, quarantine_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        self._out = output_dir
        self._qdir = quarantine_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_canonical(
        self,
        transactions: List[Transaction],
        filename: str = "canonical_transactions.csv",
    ) -> Path:
        """Write clean transactions to the canonical output file."""
        path = self._out / filename
        rows = [t.to_dict() for t in transactions]
        self._write_csv(rows, CANONICAL_COLUMNS, path)
        logger.info(
            "Canonical output: %d records → %s", len(transactions), path
        )
        return path

    def write_quarantine(
        self,
        transactions: List[Transaction],
        filename: str = "quarantine.csv",
    ) -> Path:
        """Write invalid and duplicate transactions to the quarantine file."""
        path = self._qdir / filename
        rows = [t.to_quarantine_dict() for t in transactions]
        self._write_csv(rows, QUARANTINE_COLUMNS, path)
        logger.info(
            "Quarantine output: %d records → %s", len(transactions), path
        )
        return path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_csv(
        rows: list,
        columns: List[str],
        target: Path,
    ) -> None:
        """
        Write *rows* as a CSV to *target* using an atomic rename pattern.
        Missing columns are filled with empty string.
        """
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)

        # Ensure all expected columns exist (fill any missing with None)
        for col in columns:
            if col not in df.columns:
                df[col] = None

        df = df[columns]  # Enforce column order

        # Write to temp file, then rename for atomicity
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=target.parent, prefix=".tmp_", suffix=".csv"
        )
        try:
            os.close(tmp_fd)
            df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, target)
        except Exception:
            os.unlink(tmp_path)
            raise
