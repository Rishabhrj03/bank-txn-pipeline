"""
src.deduplication.deduplicator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Two-level duplicate detection:

Level 1 – Exact-row hash
    SHA-256 of all content fields. Catches the case where the exact same
    row appears twice in the same file or across re-delivered files.

Level 2 – ref_no key
    Bank reference numbers must be globally unique. If the same ref_no is
    seen a second time (even with slightly different surrounding data, e.g.
    a balance discrepancy), the second occurrence is a duplicate.

Genuine repeated transactions
    Two payments to the same merchant for the same amount at the same time
    are NOT duplicates if they carry different ref_nos — the bank issued
    separate references, so they are separate events.

Idempotency / cross-run deduplication
    Call seed() with existing canonical records before processing new files.
    The seen-sets persist across calls within the same Deduplicator instance,
    enabling cross-file deduplication in a single pipeline run.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Set, Tuple

from ..models.transaction import Transaction, ValidationStatus

logger = logging.getLogger(__name__)


class Deduplicator:

    def __init__(self) -> None:
        # Content-hash → already seen
        self._seen_hashes: Set[str] = set()
        # ref_no → already seen
        self._seen_ref_nos: Set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def seed(self, existing: List[Transaction]) -> None:
        """
        Pre-populate the seen-sets from an already-canonical dataset.

        Call this before processing new files to achieve:
        - Idempotent re-processing (re-submitted files don't duplicate)
        - Cross-file deduplication across pipeline runs
        """
        for txn in existing:
            self._seen_hashes.add(txn.record_hash)
            if txn.ref_no:
                self._seen_ref_nos.add(txn.ref_no.strip())

        logger.info(
            "Deduplicator seeded: %d hashes, %d ref_nos",
            len(self._seen_hashes), len(self._seen_ref_nos),
        )

    def deduplicate(
        self, transactions: List[Transaction]
    ) -> Tuple[List[Transaction], List[Transaction]]:
        """
        Split *transactions* into (unique, duplicates).

        Side effect: unique records are registered in the seen-sets so that
        subsequent calls (e.g. from the next input file) can detect
        cross-file duplicates without an explicit re-seed.
        """
        unique: List[Transaction] = []
        duplicates: List[Transaction] = []

        for txn in transactions:
            reason = self._classify(txn)
            if reason:
                txn.mark_as_duplicate(reason)
                duplicates.append(txn)
            else:
                self._register(txn)
                unique.append(txn)

        logger.info(
            "Deduplication: %d unique, %d duplicates",
            len(unique), len(duplicates),
        )
        return unique, duplicates

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify(self, txn: Transaction) -> Optional[str]:
        """Return a human-readable duplicate reason, or None if unique."""
        if txn.record_hash in self._seen_hashes:
            return (
                "DUPLICATE_ROW: identical record (same hash) already exists "
                "in the canonical dataset"
            )
        ref = (txn.ref_no or "").strip()
        if ref and ref in self._seen_ref_nos:
            return (
                f"DUPLICATE_REF_NO: ref_no '{ref}' already exists "
                "in the canonical dataset — bank references must be unique"
            )
        return None

    def _register(self, txn: Transaction) -> None:
        """Mark a transaction as seen so future duplicates are caught."""
        self._seen_hashes.add(txn.record_hash)
        ref = (txn.ref_no or "").strip()
        if ref:
            self._seen_ref_nos.add(ref)
