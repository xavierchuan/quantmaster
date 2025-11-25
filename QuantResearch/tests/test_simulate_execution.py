import argparse
import tempfile
from pathlib import Path
import unittest

from scripts.simulate_execution import load_orders


class LoadOrdersFromTradesTest(unittest.TestCase):
    def _write_csv(self, directory: Path, name: str, rows: str) -> Path:
        path = directory / name
        path.write_text(rows, encoding="utf-8")
        return path

    def test_entry_exit_rows_and_pnl_assignment(self) -> None:
        rows = """ts_entry,ts_exit,symbol,direction,qty,price_entry,exit,pnl,strategy
2024-01-01T00:00:00Z,2024-01-01T01:00:00Z,EURUSD,long,1000,1.10,1.12,12.5,sma_atr
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(Path(tmpdir), "trades_sample.csv", rows)
            args = argparse.Namespace(trades_csv=str(path), orders=None, symbol=None)

            orders = load_orders(args)

            self.assertEqual(len(orders), 2)
            self.assertListEqual(list(orders["side"]), ["buy", "sell"])
            self.assertEqual(orders.iloc[0]["pnl"], 0.0)
            self.assertAlmostEqual(orders.iloc[1]["pnl"], 12.5)
            self.assertTrue((orders["strategy"] == "sma_atr").all())

    def test_symbol_inferred_from_filename_when_missing(self) -> None:
        rows = """ts_entry,ts_exit,direction,qty,price_entry,exit,pnl
2024-02-01T00:00:00Z,2024-02-01T02:00:00Z,short,5000,1.25,1.2,-15.0
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_csv(Path(tmpdir), "trades_USDJPY_sample.csv", rows)
            args = argparse.Namespace(trades_csv=str(path), orders=None, symbol=None)

            orders = load_orders(args)

            self.assertTrue((orders["symbol"] == "USDJPY").all())
            self.assertListEqual(list(orders["side"]), ["sell", "buy"])
            self.assertAlmostEqual(orders.iloc[1]["pnl"], -15.0)


if __name__ == "__main__":
    unittest.main()
