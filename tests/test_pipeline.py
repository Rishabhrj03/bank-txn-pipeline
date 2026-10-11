"""
tests/test_pipeline.py
~~~~~~~~~~~~~~~~~~~~~~
End-to-end integration tests for PipelineOrchestrator.

These tests exercise the full pipeline on tiny in-memory CSV fixtures,
verifying correct counts and output file contents.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.pipeline.orchestrator import PipelineOrchestrator
from tests.conftest import make_test_config, valid_row, write_csv


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def run(config) -> tuple:
    """Run pipeline and return (report, canonical_df, quarantine_df)."""
    report = PipelineOrchestrator(config).run()
    canon = pd.read_csv(config.paths.output_dir / "canonical_transactions.csv")
    quaran = pd.read_csv(config.paths.quarantine_dir / "quarantine.csv")
    return report, canon, quaran


# ─────────────────────────────────────────────────────────────────────────────
# Basic processing
# ─────────────────────────────────────────────────────────────────────────────

class TestBasicProcessing:
    def test_clean_batch_no_issues(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(
            cfg.paths.input_dir / "batch.csv",
            [valid_row("R001"), valid_row("R002"), valid_row("R003")],
        )
        report, canon, quaran = run(cfg)

        assert report.total_records_received == 3
        assert report.total_records_processed == 3
        assert report.total_records_rejected == 0
        assert report.total_duplicates_removed == 0
        assert len(canon) == 3
        assert len(quaran) == 0

    def test_output_columns_present(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001")])
        _, canon, _ = run(cfg)

        for col in ["account_number", "txn_date", "ref_no", "debit", "credit", "balance"]:
            assert col in canon.columns

    def test_date_normalised_to_iso(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001", txn_date="01-08-2026")])
        _, canon, _ = run(cfg)
        assert canon.iloc[0]["txn_date"] == "2026-08-01"


# ─────────────────────────────────────────────────────────────────────────────
# Validation quarantine
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationQuarantine:
    def test_invalid_date_quarantined(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(
            cfg.paths.input_dir / "batch.csv",
            [
                valid_row("R001"),
                valid_row("R002", txn_date="32-08-2026"),   # impossible day
            ],
        )
        report, canon, quaran = run(cfg)
        assert report.total_records_processed == 1
        assert report.total_records_rejected == 1
        assert len(canon) == 1
        assert "INVALID_DATE" in quaran.iloc[0]["rejection_reasons"]

    def test_both_debit_and_credit_quarantined(self, tmp_path):
        cfg = make_test_config(tmp_path)
        bad = valid_row("R002")
        bad["debit"] = "500"
        bad["credit"] = "300"
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001"), bad])
        report, _, quaran = run(cfg)
        assert report.total_records_rejected == 1
        assert "BOTH_DEBIT_AND_CREDIT" in quaran.iloc[0]["rejection_reasons"]
        assert "BOTH_DEBIT_AND_CREDIT" in report.rejection_breakdown

    def test_no_amount_quarantined(self, tmp_path):
        cfg = make_test_config(tmp_path)
        bad = valid_row("R002")
        bad["debit"] = ""
        bad["credit"] = ""
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001"), bad])
        report, _, quaran = run(cfg)
        assert report.total_records_rejected == 1
        assert "NO_DEBIT_OR_CREDIT" in quaran.iloc[0]["rejection_reasons"]

    def test_multiple_errors_on_one_row(self, tmp_path):
        """A row with invalid date AND no amount gets BOTH reasons recorded."""
        cfg = make_test_config(tmp_path)
        bad = valid_row("R002", txn_date="00-00-0000")
        bad["debit"] = ""
        bad["credit"] = ""
        write_csv(cfg.paths.input_dir / "batch.csv", [bad])
        _, _, quaran = run(cfg)
        reasons = quaran.iloc[0]["rejection_reasons"]
        assert "INVALID_DATE" in reasons
        assert "NO_DEBIT_OR_CREDIT" in reasons


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplication:
    def test_in_file_exact_duplicate_removed(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(
            cfg.paths.input_dir / "batch.csv",
            [valid_row("R001"), valid_row("R001")],   # exact duplicate
        )
        report, canon, quaran = run(cfg)
        assert report.total_records_processed == 1
        assert report.total_duplicates_removed == 1
        assert len(canon) == 1
        # Duplicate ends up in quarantine with DUPLICATE status
        assert quaran.iloc[0]["validation_status"] == "DUPLICATE"

    def test_cross_file_duplicate_removed(self, tmp_path):
        cfg = make_test_config(tmp_path)
        # File 1: unique row + the duplicate ref
        write_csv(
            cfg.paths.input_dir / "batch_2026-08-01.csv",
            [valid_row("RUNIQ"), valid_row("RDUP")],
        )
        # File 2: different content overall (different unique ref) so it's not
        # skipped by the idempotency hash check, but RDUP is a cross-file dup
        write_csv(
            cfg.paths.input_dir / "batch_2026-08-02.csv",
            [valid_row("RUNIQ2"), valid_row("RDUP")],
        )
        report, canon, _ = run(cfg)
        assert report.total_records_processed == 3   # RUNIQ + RDUP + RUNIQ2
        assert report.total_duplicates_removed == 1  # RDUP in file 2

    def test_genuine_repeated_transaction_kept(self, tmp_path):
        """Same amount/description but different ref_no → both kept."""
        cfg = make_test_config(tmp_path)
        write_csv(
            cfg.paths.input_dir / "batch.csv",
            [
                valid_row("R001", debit="500"),   # Different ref_nos
                valid_row("R002", debit="500"),   # → genuinely separate events
            ],
        )
        report, canon, _ = run(cfg)
        assert len(canon) == 2
        assert report.total_duplicates_removed == 0


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_second_run_skips_processed_files(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001"), valid_row("R002")])

        # First run
        r1 = PipelineOrchestrator(cfg).run()
        # Second run — same files, same content hashes
        r2 = PipelineOrchestrator(cfg).run()

        assert r1.total_records_processed == 2
        assert r2.total_files_skipped == 1
        assert r2.total_records_processed == 0

    def test_second_run_output_unchanged(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001"), valid_row("R002")])

        PipelineOrchestrator(cfg).run()
        c1 = pd.read_csv(cfg.paths.output_dir / "canonical_transactions.csv")

        PipelineOrchestrator(cfg).run()
        c2 = pd.read_csv(cfg.paths.output_dir / "canonical_transactions.csv")

        assert len(c1) == len(c2)
        assert set(c1["ref_no"]) == set(c2["ref_no"])

    def test_new_file_processed_on_second_run(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch_aug01.csv", [valid_row("R001")])

        PipelineOrchestrator(cfg).run()

        # Add a new file
        write_csv(cfg.paths.input_dir / "batch_aug02.csv", [valid_row("R002")])

        r2 = PipelineOrchestrator(cfg).run()
        c2 = pd.read_csv(cfg.paths.output_dir / "canonical_transactions.csv")

        assert r2.total_files_skipped == 1
        assert r2.total_records_processed == 1
        assert len(c2) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Late correction
# ─────────────────────────────────────────────────────────────────────────────

class TestLateCorrection:
    def test_late_correction_adds_new_records(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch_2026-08-01.csv", [valid_row("R001"), valid_row("R002")])
        write_csv(
            cfg.paths.input_dir / "batch_2026-08-04_late_correction.csv",
            [
                valid_row("R900", txn_date="01-08-2026"),   # new record for Aug 1
                valid_row("R901", txn_date="01-08-2026"),   # new record for Aug 1
            ],
        )
        report, canon, _ = run(cfg)
        assert len(canon) == 4   # 2 original + 2 from correction
        assert report.late_correction_impact["new_records_added"] == 2

    def test_late_correction_deduplicates_against_existing(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch_2026-08-01.csv", [valid_row("R001"), valid_row("R002")])
        write_csv(
            cfg.paths.input_dir / "batch_2026-08-04_late_correction.csv",
            [
                valid_row("R900"),          # genuinely new
                valid_row("R001"),          # dup of existing R001
            ],
        )
        report, canon, _ = run(cfg)
        assert len(canon) == 3   # 2 original + 1 new from correction
        assert report.total_duplicates_removed == 1
        assert report.late_correction_impact["duplicates_skipped"] == 1

    def test_late_correction_processed_after_regular_batches(self, tmp_path):
        """
        Even if the correction file appears first alphabetically, it must be
        processed after all regular batches so the Deduplicator has full state.
        """
        cfg = make_test_config(tmp_path)
        # Correction file name sorts before batch name alphabetically
        write_csv(
            cfg.paths.input_dir / "aaa_late_correction.csv",   # alphabetically first
            [valid_row("R001"), valid_row("R900")],
        )
        write_csv(
            cfg.paths.input_dir / "batch_2026-08-01.csv",
            [valid_row("R001"), valid_row("R002")],
        )
        report, canon, _ = run(cfg)
        # R001 in correction should be dup (batch processed first)
        assert report.total_duplicates_removed == 1
        assert len(canon) == 3   # R001, R002 (batch) + R900 (correction new)


# ─────────────────────────────────────────────────────────────────────────────
# Reconciliation report
# ─────────────────────────────────────────────────────────────────────────────

class TestReconciliationReport:
    def test_report_files_created(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001")])
        run(cfg)
        assert (cfg.paths.output_dir / "reconciliation_report.txt").exists()
        assert (cfg.paths.output_dir / "reconciliation_report.json").exists()

    def test_report_text_contains_key_sections(self, tmp_path):
        cfg = make_test_config(tmp_path)
        write_csv(cfg.paths.input_dir / "batch.csv", [valid_row("R001")])
        run(cfg)
        text = (cfg.paths.output_dir / "reconciliation_report.txt").read_text()
        assert "OVERALL SUMMARY" in text
        assert "FILE-LEVEL DETAIL" in text
        assert "Total Records Received" in text
