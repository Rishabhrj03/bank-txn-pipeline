"""
tests/test_validators.py
~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for each individual validator and the ValidationChain.
"""

from __future__ import annotations

import pytest

from src.models.transaction import ValidationStatus
from src.validation.rules import AmountValidator, DateValidator, RequiredFieldsValidator
from src.validation.chain import ValidationChain
from tests.conftest import make_transaction


# ─────────────────────────────────────────────────────────────────────────────
# DateValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestDateValidator:
    @pytest.fixture(autouse=True)
    def validator(self):
        self.v = DateValidator(
            date_formats=["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"],
            year_min=2000,
            year_max=2100,
        )

    def test_valid_dd_mm_yyyy(self):
        txn = make_transaction(txn_date_raw="01-08-2026")
        self.v.validate(txn)
        assert txn.txn_date == "2026-08-01"
        assert txn.validation_status == ValidationStatus.VALID

    def test_valid_yyyy_mm_dd(self):
        txn = make_transaction(txn_date_raw="2026-08-01")
        self.v.validate(txn)
        assert txn.txn_date == "2026-08-01"
        assert txn.validation_status == ValidationStatus.VALID

    def test_valid_slash_format(self):
        txn = make_transaction(txn_date_raw="01/08/2026")
        self.v.validate(txn)
        assert txn.txn_date == "2026-08-01"

    def test_invalid_day_32(self):
        """Day 32 is impossible — no format should accept it."""
        txn = make_transaction(txn_date_raw="32-08-2026")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID
        assert any("INVALID_DATE" in r for r in txn.rejection_reasons)

    def test_invalid_day_00(self):
        """Day 0 is impossible."""
        txn = make_transaction(txn_date_raw="00-08-2026")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID

    def test_missing_date(self):
        txn = make_transaction(txn_date_raw="")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID
        assert any("INVALID_DATE" in r for r in txn.rejection_reasons)

    def test_absurd_year_out_of_bounds(self):
        """Year 206 is outside [2000, 2100] sanity window."""
        txn = make_transaction(txn_date_raw="01-08-206")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID

    def test_invalid_month_13(self):
        txn = make_transaction(txn_date_raw="01-13-2026")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID

    def test_garbage_string(self):
        txn = make_transaction(txn_date_raw="not-a-date")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID


# ─────────────────────────────────────────────────────────────────────────────
# AmountValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestAmountValidator:
    @pytest.fixture(autouse=True)
    def validator(self):
        self.v = AmountValidator()

    def test_debit_only_valid(self):
        txn = make_transaction(debit=500.0, credit=None)
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.VALID

    def test_credit_only_valid(self):
        txn = make_transaction(debit=None, credit=5000.0)
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.VALID

    def test_both_debit_and_credit_rejected(self):
        txn = make_transaction(debit=500.0, credit=300.0)
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID
        assert any("BOTH_DEBIT_AND_CREDIT" in r for r in txn.rejection_reasons)

    def test_neither_debit_nor_credit_rejected(self):
        txn = make_transaction(debit=None, credit=None)
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID
        assert any("NO_DEBIT_OR_CREDIT" in r for r in txn.rejection_reasons)

    def test_zero_treated_as_absent(self):
        """A row with debit=0, credit=0 has effectively no amount."""
        txn = make_transaction(debit=0.0, credit=0.0)
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID

    def test_zero_debit_with_valid_credit(self):
        """0 debit should not block a valid credit."""
        txn = make_transaction(debit=0.0, credit=1000.0)
        self.v.validate(txn)
        # credit=1000 is present; debit=0 treated as absent → valid
        assert txn.validation_status == ValidationStatus.VALID


# ─────────────────────────────────────────────────────────────────────────────
# RequiredFieldsValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestRequiredFieldsValidator:
    @pytest.fixture(autouse=True)
    def validator(self):
        self.v = RequiredFieldsValidator()

    def test_all_fields_present(self):
        txn = make_transaction()
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.VALID

    def test_missing_account_number(self):
        txn = make_transaction(account_number="")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID
        assert any("account_number" in r for r in txn.rejection_reasons)

    def test_missing_ref_no(self):
        txn = make_transaction(ref_no="")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID

    def test_missing_txn_date(self):
        txn = make_transaction(txn_date_raw="")
        self.v.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID


# ─────────────────────────────────────────────────────────────────────────────
# ValidationChain — collect-all-errors behaviour
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationChain:
    @pytest.fixture(autouse=True)
    def chain(self):
        self.chain = ValidationChain([
            RequiredFieldsValidator(),
            DateValidator(),
            AmountValidator(),
        ])

    def test_valid_transaction_passes(self):
        txn = make_transaction()
        self.chain.validate(txn)
        assert txn.validation_status == ValidationStatus.VALID

    def test_multiple_errors_collected(self):
        """A row with bad date AND missing amount should carry both reasons."""
        txn = make_transaction(txn_date_raw="32-08-2026", debit=None, credit=None)
        self.chain.validate(txn)
        assert txn.validation_status == ValidationStatus.INVALID
        # At least INVALID_DATE and NO_DEBIT_OR_CREDIT
        reasons = " ".join(txn.rejection_reasons)
        assert "INVALID_DATE" in reasons
        assert "NO_DEBIT_OR_CREDIT" in reasons

    def test_validate_all_splits_correctly(self):
        txns = [
            make_transaction(ref_no="OK001"),
            make_transaction(ref_no="BAD001", txn_date_raw="99-99-9999"),
            make_transaction(ref_no="OK002", debit=None, credit=200.0),
        ]
        valid, quarantined = self.chain.validate_all(txns)
        assert len(valid) == 2
        assert len(quarantined) == 1
        assert quarantined[0].ref_no == "BAD001"
