"""
src.validation.base
~~~~~~~~~~~~~~~~~~~
Abstract base class for all validators.

Validators follow the Chain of Responsibility pattern:
- Each validator inspects/mutates the Transaction and returns it.
- Validators are composed in a ValidationChain; order matters.
- A validator MUST NOT raise — it embeds failure info into the Transaction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models.transaction import Transaction


class BaseValidator(ABC):
    """Contract for all transaction validators."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name used in logs and metrics."""
        ...

    @abstractmethod
    def validate(self, txn: Transaction) -> Transaction:
        """
        Inspect *txn*, optionally mutate validation_status / rejection_reasons,
        and return the (possibly modified) transaction.
        """
        ...
