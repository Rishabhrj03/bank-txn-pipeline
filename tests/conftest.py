"""
tests/conftest.py
~~~~~~~~~~~~~~~~~
Shared fixtures for the test suite.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.models.transaction import Transaction, ValidationStatus


# ─────────────────────────────────────────────────────────────────────────────
# Transaction builder helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_transaction(**overrides) -> Transaction:
    """Build a minimal valid Transaction; override any field via kwargs."""
    defaults: Dict[str, Any] = dict(
        account_number="AC100000001",
        txn_date_raw="01-08-2026",
        txn_time="10:00:00",
        description="TEST TRANSACTION",
        ref_no="REF000001",
        debit=500.0,
        credit=None,
        balance=9500.0,
        account_type="SAVINGS",
        source_file="test_batch.csv",
        row_number=2,
    )
    defaults.update(overrides)
    return Transaction(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# CSV helpers
# ─────────────────────────────────────────────────────────────────────────────

HEADER = [
    "account_number", "txn_date", "txn_time", "description",
    "ref_no", "debit", "credit", "balance", "account_type",
]


def write_csv(path: Path, rows: List[Dict[str, str]]) -> None:
    """Write a minimal CSV file with the given rows (dict per row)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = rows[0].keys() if rows else HEADER
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def valid_row(
    ref_no: str = "REF001",
    txn_date: str = "01-08-2026",
    debit: str = "1000",
    credit: str = "",
    account: str = "AC100000001",
) -> Dict[str, str]:
    """Return a valid CSV row dict (all strings, as pandas reads them)."""
    return {
        "account_number": account,
        "txn_date": txn_date,
        "txn_time": "10:00:00",
        "description": "TEST PAYMENT",
        "ref_no": ref_no,
        "debit": debit,
        "credit": credit,
        "balance": "50000",
        "account_type": "SAVINGS",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Config factory
# ─────────────────────────────────────────────────────────────────────────────

def make_test_config(tmp_path: Path):
    """Create a PipelineConfig pointing entirely at tmp_path."""
    from src.config import PipelineConfig

    cfg_yaml = tmp_path / "test_config.yaml"
    cfg_yaml.write_text(
        f"""
paths:
  input_dir: "{tmp_path / 'input'}"
  output_dir: "{tmp_path / 'output'}"
  quarantine_dir: "{tmp_path / 'quarantine'}"
  state_dir: "{tmp_path / 'state'}"
input:
  file_pattern: "*.csv"
  late_correction_pattern: "*late_correction*"
  excluded_files: []
validation:
  date_formats:
    - "%d-%m-%Y"
    - "%Y-%m-%d"
  year_min: 2000
  year_max: 2100
  required_columns:
    - account_number
    - txn_date
    - ref_no
deduplication:
  primary_key: ref_no
output:
  canonical_file: canonical_transactions.csv
  quarantine_file: quarantine.csv
  reconciliation_report: reconciliation_report
""",
        encoding="utf-8",
    )
    return PipelineConfig.from_yaml(cfg_yaml, tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# Pytest fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def valid_txn():
    return make_transaction()


@pytest.fixture
def temp_config(tmp_path):
    return make_test_config(tmp_path)
