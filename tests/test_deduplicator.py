"""
tests/test_deduplicator.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the Deduplicator (both dedup levels).
"""

from __future__ import annotations

import pytest

from src.deduplication.deduplicator import Deduplicator
from src.models.transaction import ValidationStatus
from tests.conftest import make_transaction


def txn(ref_no="REF001", account="AC001", debit=500.0, credit=None, txn_date="01-08-2026"):
    return make_transaction(
        ref_no=ref_no,
        account_number=account,
        debit=debit,
        credit=credit,
        txn_date_raw=txn_date,
    )


class TestDeduplicator:

    @pytest.fixture(autouse=True)
    def fresh_dedup(self):
        self.d = Deduplicator()

    # ── No duplicates ─────────────────────────────────────────────────

    def test_no_duplicates_all_pass(self):
        txns = [txn("REF001"), txn("REF002"), txn("REF003")]
        unique, dupes = self.d.deduplicate(txns)
        assert len(unique) == 3
        assert len(dupes) == 0

    # ── Exact-row hash dedup ──────────────────────────────────────────

    def test_exact_duplicate_within_batch(self):
        """Same row appearing twice in the same file → second is dup."""
        t1 = txn("REF001")
        t2 = txn("REF001")  # Identical content → same hash AND same ref_no
        unique, dupes = self.d.deduplicate([t1, t2])
        assert len(unique) == 1
        assert len(dupes) == 1
        assert any("DUPLICATE" in r for r in dupes[0].rejection_reasons)
        assert dupes[0].validation_status == ValidationStatus.DUPLICATE

    # ── ref_no-level dedup ────────────────────────────────────────────

    def test_same_ref_no_different_content(self):
        """Same ref_no but different amount: ref_no uniqueness violated."""
        t1 = txn("REF001", debit=500.0)
        t2 = txn("REF001", debit=600.0)   # Different amount → different hash
        unique, dupes = self.d.deduplicate([t1, t2])
        assert len(unique) == 1
        assert len(dupes) == 1
        assert any("DUPLICATE_REF_NO" in r for r in dupes[0].rejection_reasons)

    # ── Genuine repeated transactions (should NOT be deduped) ─────────

    def test_genuine_repeated_transaction_not_deduped(self):
        """
        Two payments to the same merchant for the same amount at the same time
        but with DIFFERENT ref_nos → different bank events, both kept.
        """
        t1 = txn("REF001", debit=500.0)
        t2 = txn("REF002", debit=500.0)   # Different ref_no!
        unique, dupes = self.d.deduplicate([t1, t2])
        assert len(unique) == 2
        assert len(dupes) == 0

    # ── Seed-based cross-run dedup ────────────────────────────────────

    def test_seed_prevents_cross_run_duplicate(self):
        """Seeding with existing records catches re-submitted files."""
        existing = txn("REF001")
        self.d.seed([existing])

        new_txn = txn("REF001")  # Same ref — would create a dup
        unique, dupes = self.d.deduplicate([new_txn])
        assert len(unique) == 0
        assert len(dupes) == 1

    def test_seed_does_not_block_new_records(self):
        """Records not in the seed set should pass through normally."""
        existing = txn("REF001")
        self.d.seed([existing])

        truly_new = txn("REF002")
        unique, dupes = self.d.deduplicate([truly_new])
        assert len(unique) == 1
        assert len(dupes) == 0

    # ── Stateful cross-file dedup ─────────────────────────────────────

    def test_stateful_across_multiple_calls(self):
        """
        Deduplicator retains state across calls — simulates processing
        multiple files in sequence.
        """
        file1_txns = [txn("REF001"), txn("REF002")]
        file2_txns = [txn("REF001"), txn("REF003")]  # REF001 is a cross-file dup

        unique1, dupes1 = self.d.deduplicate(file1_txns)
        unique2, dupes2 = self.d.deduplicate(file2_txns)

        assert len(unique1) == 2
        assert len(dupes1) == 0
        assert len(unique2) == 1   # Only REF003 is new
        assert len(dupes2) == 1    # REF001 is a cross-file dup

    # ── Marking ───────────────────────────────────────────────────────

    def test_duplicate_marked_with_status(self):
        t1, t2 = txn("REF001"), txn("REF001")
        _, dupes = self.d.deduplicate([t1, t2])
        assert dupes[0].validation_status == ValidationStatus.DUPLICATE

    def test_empty_batch_ok(self):
        unique, dupes = self.d.deduplicate([])
        assert unique == []
        assert dupes == []

    def test_empty_ref_no_not_deduped_by_ref(self):
        """Two rows with blank ref_no should not incorrectly share the '' key."""
        t1 = make_transaction(ref_no="", debit=100.0)
        t2 = make_transaction(ref_no="", debit=200.0)
        # Different content → different hashes; blank ref_no not registered
        unique, dupes = self.d.deduplicate([t1, t2])
        # Both have unique hashes (different amounts) and blank ref_no is not tracked
        assert len(unique) == 2
