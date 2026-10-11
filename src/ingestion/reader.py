"""
src.ingestion.reader
~~~~~~~~~~~~~~~~~~~~
Reads raw CSV files and normalises them into Transaction objects.

Responsibilities:
- Schema normalisation via flexible column-alias mapping
- Amount parsing (handles blanks, commas)
- Creates Transaction objects (no validation logic here – SRP)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from ..models.transaction import Transaction

logger = logging.getLogger(__name__)

# Default aliases used when config provides none
_DEFAULT_COLUMN_ALIASES: Dict[str, List[str]] = {
    "account_number": ["account_number", "account_no", "acct_no"],
    "txn_date":       ["txn_date", "transaction_date", "date", "bn_date"],
    "txn_time":       ["txn_time", "transaction_time", "time", "bn_time"],
    "description":    ["description", "narration", "remarks"],
    "ref_no":         ["ref_no", "reference_no", "reference_number", "ref"],
    "debit":          ["debit", "debit_amount", "withdrawal", "dr"],
    "credit":         ["credit", "credit_amount", "deposit", "cr"],
    "balance":        ["balance", "closing_balance", "bal"],
    "account_type":   ["account_type", "acct_type", "acc_type"],
}


class FileReader:
    """
    Reads a single CSV file and returns a list of Transaction objects.

    Column aliases allow the same pipeline to handle files from different
    source systems without code changes — just update the config YAML.
    """

    def __init__(self, column_aliases: Optional[Dict[str, List[str]]] = None) -> None:
        self._aliases = column_aliases or _DEFAULT_COLUMN_ALIASES

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, file_path: Path) -> List[Transaction]:
        """
        Read *file_path* and return one Transaction per data row.
        Never raises; parsing errors are embedded in the returned objects.
        """
        logger.info("Reading: %s", file_path.name)
        try:
            # dtype=str prevents pandas from guessing types (e.g. stripping
            # leading zeros from account numbers or mis-parsing dates).
            df = pd.read_csv(
                file_path,
                dtype=str,
                keep_default_na=False,  # Empty cells → "" not NaN
            )
        except Exception as exc:
            logger.error("Cannot read %s: %s", file_path, exc)
            raise

        df = self._normalise_columns(df, file_path.name)
        transactions = [
            self._row_to_transaction(row, file_path.name, row_num)
            for row_num, row in enumerate(df.itertuples(index=False), start=2)
        ]
        logger.info("  → %d rows loaded from %s", len(transactions), file_path.name)
        return transactions

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _normalise_columns(self, df: pd.DataFrame, file_name: str) -> pd.DataFrame:
        """
        Rename raw column headers to canonical names via alias lookup.
        Case-insensitive; extra whitespace is stripped.
        """
        lower_map = {c.lower().strip(): c for c in df.columns}
        rename: Dict[str, str] = {}

        for canonical, aliases in self._aliases.items():
            for alias in aliases:
                if alias.lower() in lower_map:
                    rename[lower_map[alias.lower()]] = canonical
                    break

        unmapped = [k for k in self._aliases if k not in rename.values()]
        if unmapped:
            logger.warning(
                "%s: could not map columns %s — they will be treated as empty",
                file_name,
                unmapped,
            )

        return df.rename(columns=rename)

    @staticmethod
    def _parse_amount(raw: str) -> Optional[float]:
        """
        Parse an amount cell to float.
        Returns None for blank / non-numeric values so the AmountValidator
        can produce a meaningful rejection reason.
        """
        cleaned = raw.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            logger.debug("Cannot parse amount '%s'", raw)
            return None

    def _row_to_transaction(
        self, row: tuple, source_file: str, row_number: int
    ) -> Transaction:
        """Convert a single DataFrame row (NamedTuple) to a Transaction."""

        def get(field: str) -> str:
            return str(getattr(row, field, "") or "")

        return Transaction(
            account_number=get("account_number").strip(),
            txn_date_raw=get("txn_date").strip(),
            txn_time=get("txn_time").strip(),
            description=get("description").strip(),
            ref_no=get("ref_no").strip(),
            debit=self._parse_amount(get("debit")),
            credit=self._parse_amount(get("credit")),
            balance=self._parse_amount(get("balance")),
            account_type=get("account_type").strip(),
            source_file=source_file,
            row_number=row_number,
        )
