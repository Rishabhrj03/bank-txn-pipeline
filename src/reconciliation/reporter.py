"""
src.reconciliation.reporter
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Builds and persists the reconciliation report.

Outputs:
  reconciliation_report.txt  — human-readable summary for ops/data teams
  reconciliation_report.json — machine-readable version for downstream
                               dashboards / audit systems
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..models.transaction import Transaction

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data-transfer objects
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FileStats:
    """Per-file processing statistics."""
    file_name: str
    total_records: int = 0
    valid_records: int = 0
    quarantined_records: int = 0
    duplicate_records: int = 0
    skipped_already_processed: bool = False
    is_late_correction: bool = False
    late_correction_new_records: int = 0


@dataclass
class ReconciliationReport:
    """
    Complete reconciliation summary for one pipeline run.

    Design: all mutation goes through add_file_stats() so tallies stay
    consistent without explicit bookkeeping in the orchestrator.
    """

    run_id: str
    generated_at: str

    # ── Grand totals ───────────────────────────────────────────────────
    total_records_received: int = 0
    total_records_processed: int = 0
    total_records_rejected: int = 0
    total_duplicates_removed: int = 0
    total_files_processed: int = 0
    total_files_skipped: int = 0

    # ── Breakdown ──────────────────────────────────────────────────────
    rejection_breakdown: Dict[str, int] = field(default_factory=dict)
    file_stats: List[FileStats] = field(default_factory=list)
    late_correction_impact: Dict[str, object] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_file_stats(self, stats: FileStats) -> None:
        """Register per-file stats and update grand totals."""
        self.file_stats.append(stats)
        if stats.skipped_already_processed:
            self.total_files_skipped += 1
            return

        self.total_files_processed += 1
        self.total_records_received += stats.total_records
        self.total_records_processed += stats.valid_records
        self.total_records_rejected += stats.quarantined_records
        self.total_duplicates_removed += stats.duplicate_records

        if stats.is_late_correction:
            self.late_correction_impact = {
                "source_file": stats.file_name,
                "new_records_added": stats.late_correction_new_records,
                "duplicates_skipped": stats.duplicate_records,
                "records_quarantined": stats.quarantined_records,
            }

    def tally_rejection_reasons(self, quarantined: List[Transaction]) -> None:
        """Aggregate rejection-reason counts from the quarantine list."""
        for txn in quarantined:
            for reason in txn.rejection_reasons:
                # Normalise to the short key (before the first ':')
                key = reason.split(":")[0].strip()
                self.rejection_breakdown[key] = (
                    self.rejection_breakdown.get(key, 0) + 1
                )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["file_stats"] = [asdict(s) for s in self.file_stats]
        return d

    def to_text(self) -> str:
        sep = "=" * 65
        thin = "-" * 45

        lines = [
            sep,
            "  BANK TRANSACTION PIPELINE  –  RECONCILIATION REPORT",
            sep,
            f"  Run ID        : {self.run_id}",
            f"  Generated At  : {self.generated_at}",
            sep,
            "",
            "  OVERALL SUMMARY",
            "  " + thin,
            f"  Files Processed (new)    : {self.total_files_processed}",
            f"  Files Skipped (idempotent): {self.total_files_skipped}",
            f"  Total Records Received   : {self.total_records_received}",
            f"  Total Records Processed  : {self.total_records_processed}",
            f"  Total Records Rejected   : {self.total_records_rejected}",
            f"  Total Duplicates Removed : {self.total_duplicates_removed}",
            "",
            "  REJECTION BREAKDOWN",
            "  " + thin,
        ]

        if self.rejection_breakdown:
            for reason, count in sorted(self.rejection_breakdown.items()):
                lines.append(f"    {reason:<42}: {count}")
        else:
            lines.append("    (none)")

        lines += ["", "  FILE-LEVEL DETAIL", "  " + thin]

        for fs in self.file_stats:
            tag = " [LATE CORRECTION]" if fs.is_late_correction else ""
            status = "SKIPPED (already processed)" if fs.skipped_already_processed else "PROCESSED"
            lines += [
                f"  {fs.file_name}{tag}  [{status}]",
                f"    Total Input : {fs.total_records}",
                f"    Valid       : {fs.valid_records}",
                f"    Quarantined : {fs.quarantined_records}",
                f"    Duplicates  : {fs.duplicate_records}",
                "",
            ]

        if self.late_correction_impact:
            lci = self.late_correction_impact
            lines += [
                "  LATE CORRECTION FILE IMPACT",
                "  " + thin,
                f"    Source File          : {lci.get('source_file')}",
                f"    New Records Added    : {lci.get('new_records_added')}",
                f"    Duplicates Skipped   : {lci.get('duplicates_skipped')}",
                f"    Records Quarantined  : {lci.get('records_quarantined')}",
                "",
            ]

        lines.append(sep)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Generator
# ─────────────────────────────────────────────────────────────────────────────

class ReportGenerator:
    """Persists a ReconciliationReport as .txt and .json."""

    def __init__(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._dir = output_dir

    def save(
        self,
        report: ReconciliationReport,
        base_name: str = "reconciliation_report",
    ) -> None:
        txt_path = self._dir / f"{base_name}.txt"
        txt_path.write_text(report.to_text(), encoding="utf-8")
        logger.info("Report (text) : %s", txt_path)

        json_path = self._dir / f"{base_name}.json"
        json_path.write_text(
            json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        logger.info("Report (JSON) : %s", json_path)
