"""Tests for budget tracking."""

from kindex.budget import BudgetLedger
from kindex.config import BudgetConfig


class TestBudgetLedger:
    def test_empty(self, tmp_path):
        ledger = BudgetLedger(tmp_path / "budget.yaml", BudgetConfig())
        assert ledger.today_spend == 0
        assert ledger.can_spend()

    def test_record_and_check(self, tmp_path):
        ledger = BudgetLedger(tmp_path / "budget.yaml", BudgetConfig(daily=0.01))
        ledger.record(0.005, model="test", purpose="test")
        assert ledger.today_spend == 0.005
        assert ledger.can_spend()

        ledger.record(0.006, model="test", purpose="test")
        assert not ledger.can_spend()

    def test_persistence(self, tmp_path):
        path = tmp_path / "budget.yaml"
        ledger = BudgetLedger(path, BudgetConfig())
        ledger.record(0.001)

        ledger2 = BudgetLedger(path, BudgetConfig())
        assert ledger2.today_spend == 0.001

    def test_summary(self, tmp_path):
        ledger = BudgetLedger(tmp_path / "budget.yaml", BudgetConfig())
        s = ledger.summary()
        assert "today" in s
        assert "week" in s
        assert "month" in s
        assert s["can_spend"] is True
