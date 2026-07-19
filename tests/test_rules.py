import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from saqr.database import connect
from saqr.utils import extract_capacity_kg, excel_datetime


class TestRuleUtilities(unittest.TestCase):
    def test_capacity_with_space(self):
        self.assertEqual(extract_capacity_kg("REPASSE AZ.VDS.S/CAROCO BR 130 KG"), 130.0)

    def test_capacity_with_hyphen(self):
        self.assertEqual(extract_capacity_kg("AZ.VDS.MANZ. R/PIM.240/280 2-BR 140KG DP"), 140.0)

    def test_last_kg_is_used(self):
        self.assertEqual(extract_capacity_kg("PRODUTO 120G - BR 150KG"), 150.0)

    def test_capacity_decimal(self):
        self.assertEqual(extract_capacity_kg("REPASSE TESTE BR 172,5 KG"), 172.5)

    def test_excel_datetime(self):
        dt = excel_datetime(46213, 8 / 24)
        self.assertEqual(dt, datetime(2026, 7, 10, 8, 0))


class TestDomainSchema(unittest.TestCase):
    def test_canonical_tables_and_dependency_lineage_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            conn = connect(db)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = {
                "operational_days", "production_orders", "events", "resource_executions",
                "lines", "resource_centers", "products", "components", "families",
                "classifications", "movements", "loss_standards", "indicator_results",
                "indicator_lineage", "indicator_dependencies", "audit_log",
            }
            self.assertTrue(expected.issubset(tables))
            conn.close()

    def test_no_duplicate_fact_tables_for_business_specializations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            conn = connect(db)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for forbidden in {"production", "consumption", "loss", "repasse"}:
                self.assertNotIn(forbidden, tables)
            views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
            self.assertTrue({"production_facts", "consumption_facts", "loss_facts", "repasse_facts"}.issubset(views))
            conn.close()


if __name__ == "__main__":
    unittest.main()
