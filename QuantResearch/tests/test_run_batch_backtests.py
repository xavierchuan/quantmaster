import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]


class BatchBacktestCLITest(unittest.TestCase):
    def test_batch_runner_applies_scenario(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "results"
            cmd = [
                sys.executable,
                "scripts/run_batch_backtests.py",
                "--schedule",
                "tests/fixtures/batch_schedule.yaml",
                "--out",
                str(out_dir),
                "--scenario-file",
                "config/stress_scenarios.yaml",
            ]
            subprocess.run(cmd, cwd=BASE_DIR, check=True)

            csv_files = sorted(out_dir.glob("batch_backtests_*.csv"))
            self.assertTrue(csv_files, "Batch runner did not produce CSV output")
            df = pd.read_csv(csv_files[-1])
            self.assertEqual(df.loc[0, "scenario"], "double_costs")
            self.assertAlmostEqual(df.loc[0, "stress_cost_spread_mult"], 2.0)
            self.assertGreaterEqual(df.loc[0, "stress_cost_comm_mult"], 2.0)


if __name__ == "__main__":
    unittest.main()
