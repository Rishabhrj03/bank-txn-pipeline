"""
src.validation.chain
~~~~~~~~~~~~~~~~~~~~
ValidationChain — composes validators in Chain of Responsibility style.

All validators always run (collect-all-errors approach).
This gives the data team complete rejection metadata rather than
stopping at the first problem.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from ..models.transaction import Transaction, ValidationStatus
from .base import BaseValidator

logger = logging.getLogger(__name__)


class ValidationChain:
    """
    Runs a fixed ordered list of validators against each transaction.
    Returns two lists: valid and quarantined (invalid + duplicates flagged
    by validators — note: business-level deduplication happens separately).
    """

    def __init__(self, validators: List[BaseValidator]) -> None:
        self._validators = validators
        logger.debug(
            "ValidationChain: [%s]",
            ", ".join(v.name for v in validators),
        )

    def validate(self, txn: Transaction) -> Transaction:
        """Run the full chain against a single transaction."""
        for validator in self._validators:
            validator.validate(txn)
        return txn

    def validate_all(
        self, transactions: List[Transaction]
    ) -> Tuple[List[Transaction], List[Transaction]]:
        """
        Validate every transaction in *transactions*.

        Returns
        -------
        valid : list[Transaction]
            Transactions that passed all validators.
        quarantined : list[Transaction]
            Transactions with at least one rejection reason.
        """
        valid: List[Transaction] = []
        quarantined: List[Transaction] = []

        for txn in transactions:
            self.validate(txn)
            if txn.validation_status == ValidationStatus.VALID:
                valid.append(txn)
            else:
                quarantined.append(txn)

        logger.info(
            "Validation: %d valid, %d quarantined",
            len(valid), len(quarantined),
        )
        return valid, quarantined
