import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


BASE_DIR = Path(__file__).resolve().parents[1]


class MonteCarloCLITest(unittest.TestCase):
    def test_cli_generates_summary_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run"
            run_dir.mkdir()
            summary_path = run_dir / "summary.json"
            summary_payload = {
                "artifacts": {
                    "equity": "tests/fixtures/equity_dummy.csv",
                }
            }
            summary_path.write_text(json.dumps(summary_payload), encoding="utf-8")

            cmd = [
                sys.executable,
                "scripts/run_monte_carlo.py",
                "--run",
                str(run_dir),
                "--iterations",
                "5",
                "--method",
                "bootstrap",
                "--seed",
                "123",
                "--scenario",
                "vol_shock",
                "--scenario-file",
                "config/stress_scenarios.yaml",
            ]
            subprocess.run(cmd, cwd=BASE_DIR, check=True)

            stress_dir = run_dir / "stress"
            summary_file = stress_dir / "mc_summary.json"
            iterations_file = stress_dir / "mc_iterations.csv"
            self.assertTrue(summary_file.exists(), "Monte Carlo summary was not created")
            self.assertTrue(iterations_file.exists(), "Monte Carlo iterations CSV missing")

            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            self.assertEqual(summary["scenario"], "vol_shock")
            self.assertEqual(summary["seed"], 123)
            self.assertEqual(summary["iterations"], 5)
            self.assertEqual(summary["method"], "bootstrap")
            overrides = summary.get("scenario_overrides")
            self.assertIsNotNone(overrides)
            self.assertAlmostEqual(overrides.get("return_scale"), 0.9)
            self.assertIn("sharpe", summary)


if __name__ == "__main__":
    unittest.main()
