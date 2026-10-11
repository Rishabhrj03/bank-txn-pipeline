"""
src.pipeline.orchestrator
~~~~~~~~~~~~~~~~~~~~~~~~~
PipelineOrchestrator — the top-level coordinator.

Execution flow
--------------
1.  Generate a unique run_id; open a run record in state DB.
2.  Load existing canonical output (if any) → seed Deduplicator for
    cross-run idempotency.
3.  Discover input files; separate regular batches from late corrections.
    Regular files are sorted lexicographically (file names embed dates).
    Late correction files are always processed last.
4.  For each file:
    a. Compute SHA-256 → check state DB → skip if already processed.
    b. Read & normalise via FileReader.
    c. Validate via ValidationChain (RequiredFields → Date → Amount).
    d. Deduplicate valid records via Deduplicator.
    e. Accumulate canonical + quarantine lists.
    f. Record file stats in state DB.
5.  Write canonical CSV (complete snapshot, not append).
6.  Write quarantine CSV.
7.  Tally rejection reasons; save reconciliation report (txt + json).
8.  Mark run as COMPLETED in state DB.

Idempotency guarantee
---------------------
- Files already in state DB (same content hash) are skipped.
- On re-run with new/changed files, only those files are processed;
  the Deduplicator is seeded with existing canonical so no duplication occurs.
- Final output files are always a complete snapshot — they are fully
  rewritten, not appended to.
"""

from __future__ import annotations

import fnmatch
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from ..config import PipelineConfig
from ..deduplication.deduplicator import Deduplicator
from ..ingestion.reader import FileReader
from ..models.transaction import Transaction, ValidationStatus
from ..output.writer import TransactionWriter
from ..reconciliation.reporter import (
    FileStats,
    ReconciliationReport,
    ReportGenerator,
)
from ..state.manager import StateManager
from ..validation.chain import ValidationChain
from ..validation.rules import AmountValidator, DateValidator, RequiredFieldsValidator

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Coordinates the full ingestion pipeline for one execution."""

    def __init__(self, config: PipelineConfig) -> None:
        self._cfg = config

        self._reader = FileReader(
            column_aliases=config.input.column_aliases or None
        )
        self._validator = ValidationChain([
            RequiredFieldsValidator(),
            DateValidator(
                date_formats=config.validation.date_formats,
                year_min=config.validation.year_min,
                year_max=config.validation.year_max,
            ),
            AmountValidator(),
        ])
        self._deduplicator = Deduplicator()
        self._state = StateManager(config.paths.state_dir)
        self._writer = TransactionWriter(
            config.paths.output_dir,
            config.paths.quarantine_dir,
        )
        self._reporter = ReportGenerator(config.paths.output_dir)

        # Accumulated output for the whole run
        self._canonical: List[Transaction] = []
        self._quarantine: List[Transaction] = []
        self._new_quarantine: List[Transaction] = []

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> ReconciliationReport:
        run_id = str(uuid.uuid4())
        logger.info("Pipeline run started  run_id=%s", run_id)
        self._state.create_run(run_id)

        report = ReconciliationReport(
            run_id=run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        # Step 1: seed from existing canonical and quarantine so re-runs don't duplicate or drop records
        self._load_existing_canonical()
        self._load_existing_quarantine()

        # Step 2: discover & classify input files
        regular, corrections = self._discover_files()

        # Step 3: process regular batches (chronological order)
        for f in regular:
            stats = self._process_file(f, is_late_correction=False)
            report.add_file_stats(stats)

        # Step 4: process late corrections last
        for f in corrections:
            stats = self._process_file(f, is_late_correction=True)
            report.add_file_stats(stats)

        # Step 5: write outputs
        self._writer.write_canonical(
            self._canonical, self._cfg.output.canonical_file
        )
        self._writer.write_quarantine(
            self._quarantine, self._cfg.output.quarantine_file
        )

        # Step 6: reconciliation report
        report.tally_rejection_reasons(self._new_quarantine)
        self._reporter.save(report, self._cfg.output.reconciliation_report)

        self._state.complete_run(run_id, report.to_dict())

        logger.info(
            "Pipeline run completed  run_id=%s  "
            "processed=%d  rejected=%d  duplicates=%d",
            run_id,
            report.total_records_processed,
            report.total_records_rejected,
            report.total_duplicates_removed,
        )
        return report

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def _discover_files(self) -> Tuple[List[Path], List[Path]]:
        """
        Glob for CSV files in input_dir; split into regular vs corrections.
        Excluded file names (e.g. NOTES.txt) are silently skipped.
        """
        excluded = {
            n.lower() for n in self._cfg.input.excluded_files
        }
        all_csvs = sorted(
            self._cfg.paths.input_dir.glob(self._cfg.input.file_pattern)
        )

        regular: List[Path] = []
        corrections: List[Path] = []

        for f in all_csvs:
            if f.name.lower() in excluded:
                continue
            if fnmatch.fnmatch(f.name.lower(), self._cfg.input.late_correction_pattern.lower()):
                corrections.append(f)
            else:
                regular.append(f)

        logger.info(
            "Discovered: %d regular file(s), %d correction file(s)",
            len(regular), len(corrections),
        )
        return regular, corrections

    # ------------------------------------------------------------------
    # Single-file processing
    # ------------------------------------------------------------------

    def _process_file(self, path: Path, *, is_late_correction: bool) -> FileStats:
        stats = FileStats(
            file_name=path.name,
            is_late_correction=is_late_correction,
        )

        # Idempotency guard
        file_hash = StateManager.compute_file_hash(path)
        if self._state.is_file_processed(file_hash):
            logger.info(
                "Skipping already-processed file: %s (hash %s…)",
                path.name, file_hash[:12],
            )
            stats.skipped_already_processed = True
            return stats

        # Read
        transactions = self._reader.read(path)
        stats.total_records = len(transactions)

        # Validate → split valid / quarantine
        valid, quarantined = self._validator.validate_all(transactions)
        stats.quarantined_records = len(quarantined)

        # Deduplicate valid records
        unique, duplicates = self._deduplicator.deduplicate(valid)
        stats.valid_records = len(unique)
        stats.duplicate_records = len(duplicates)

        if is_late_correction:
            stats.late_correction_new_records = len(unique)

        # Accumulate
        self._canonical.extend(unique)
        self._quarantine.extend(quarantined)
        self._quarantine.extend(duplicates)   # Duplicates also go to quarantine
        self._new_quarantine.extend(quarantined)
        self._new_quarantine.extend(duplicates)

        # Persist state
        self._state.mark_file_processed(
            file_name=path.name,
            file_hash=file_hash,
            record_count=stats.total_records,
            valid_count=stats.valid_records,
            quarantine_count=stats.quarantined_records,
            duplicate_count=stats.duplicate_records,
        )

        logger.info(
            "%-45s total=%-4d valid=%-4d quarantined=%-4d duplicates=%-4d",
            path.name,
            stats.total_records,
            stats.valid_records,
            stats.quarantined_records,
            stats.duplicate_records,
        )
        return stats

    # ------------------------------------------------------------------
    # Canonical seed loader (idempotency across runs)
    # ------------------------------------------------------------------

    def _load_existing_canonical(self) -> None:
        """
        If a canonical output file already exists from a previous run,
        load it and seed the Deduplicator so new runs don't re-add records.

        We read it directly with pandas (bypassing the full FileReader pipeline)
        to preserve the original source_file metadata and avoid re-validation.
        """
        canonical_path = (
            self._cfg.paths.output_dir / self._cfg.output.canonical_file
        )
        if not canonical_path.exists():
            logger.info("No existing canonical file — starting fresh.")
            return

        try:
            df = pd.read_csv(canonical_path, dtype=str, keep_default_na=False)
            transactions = []
            for _, row in df.iterrows():
                txn = self._df_row_to_transaction(row)
                if txn:
                    transactions.append(txn)

            self._deduplicator.seed(transactions)
            self._canonical = transactions
            logger.info(
                "Loaded %d existing canonical records from %s",
                len(transactions), canonical_path.name,
            )
        except Exception as exc:
            logger.warning(
                "Could not load existing canonical file (%s) — starting fresh: %s",
                canonical_path, exc,
            )

    @staticmethod
    def _df_row_to_transaction(row: pd.Series) -> Optional[Transaction]:
        """Reconstruct a Transaction from a row in the canonical CSV."""
        def g(col: str) -> str:
            val = row.get(col, "")
            return str(val) if val not in ("", "nan", None) else ""

        def gf(col: str) -> Optional[float]:
            val = row.get(col, "")
            if str(val) in ("", "nan", "None", None):
                return None
            try:
                return float(val)
            except ValueError:
                return None

        try:
            return Transaction(
                account_number=g("account_number"),
                txn_date_raw=g("txn_date"),     # normalised date becomes new raw
                txn_time=g("txn_time"),
                description=g("description"),
                ref_no=g("ref_no"),
                debit=gf("debit"),
                credit=gf("credit"),
                balance=gf("balance"),
                account_type=g("account_type"),
                source_file=g("source_file") or "canonical_transactions.csv",
                row_number=0,
                txn_date=g("txn_date"),
            )
        except Exception as exc:
            logger.debug("Skipping bad canonical row: %s", exc)
            return None

    def _load_existing_quarantine(self) -> None:
        """
        If a quarantine output file already exists from a previous run,
        load it so that re-runs or incremental runs preserve all previous
        quarantine records.
        """
        quarantine_path = (
            self._cfg.paths.quarantine_dir / self._cfg.output.quarantine_file
        )
        if not quarantine_path.exists():
            return

        try:
            df = pd.read_csv(quarantine_path, dtype=str, keep_default_na=False)
            transactions = []
            for _, row in df.iterrows():
                txn = self._df_row_to_quarantine_transaction(row)
                if txn:
                    transactions.append(txn)
            self._quarantine = transactions
            logger.info(
                "Loaded %d existing quarantine records from %s",
                len(transactions), quarantine_path.name,
            )
        except Exception as exc:
            logger.warning(
                "Could not load existing quarantine file (%s): %s",
                quarantine_path, exc,
            )

    @staticmethod
    def _df_row_to_quarantine_transaction(row: pd.Series) -> Optional[Transaction]:
        """Reconstruct a Transaction from a row in the quarantine CSV."""
        def g(col: str) -> str:
            val = row.get(col, "")
            return str(val) if val not in ("", "nan", None) else ""

        def gf(col: str) -> Optional[float]:
            val = row.get(col, "")
            if str(val) in ("", "nan", "None", None):
                return None
            try:
                return float(val)
            except ValueError:
                return None

        try:
            status_str = g("validation_status") or "INVALID"
            status = (
                ValidationStatus.DUPLICATE
                if status_str == "DUPLICATE"
                else ValidationStatus.INVALID
            )
            reasons_raw = g("rejection_reasons")
            reasons = [r.strip() for r in reasons_raw.split(";") if r.strip()]

            txn = Transaction(
                account_number=g("account_number"),
                txn_date_raw=g("txn_date_raw") or g("txn_date"),
                txn_time=g("txn_time"),
                description=g("description"),
                ref_no=g("ref_no"),
                debit=gf("debit"),
                credit=gf("credit"),
                balance=gf("balance"),
                account_type=g("account_type"),
                source_file=g("source_file") or "quarantine.csv",
                row_number=int(row.get("row_number", 0) or 0),
                txn_date=g("txn_date") or None,
                validation_status=status,
                rejection_reasons=reasons,
            )
            if g("record_hash"):
                txn.record_hash = g("record_hash")
            return txn
        except Exception as exc:
            logger.debug("Skipping bad quarantine row: %s", exc)
            return None
