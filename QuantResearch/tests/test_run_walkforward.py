import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


BASE_DIR = Path(__file__).resolve().parents[1]


class WalkForwardCLITest(unittest.TestCase):
    def test_walkforward_cli_creates_metrics_and_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "results"
            cmd = [
                sys.executable,
                "scripts/run_walkforward.py",
                "--config",
                "tests/fixtures/walkforward_config.yaml",
                "--train-bars",
                "4",
                "--test-bars",
                "3",
                "--step-bars",
                "3",
                "--max-windows",
                "2",
                "--output-root",
                str(output_root),
            ]
            subprocess.run(cmd, cwd=BASE_DIR, check=True)

            wf_dirs = list((output_root).glob("walkforward_*"))
            self.assertTrue(wf_dirs, "Walk-forward output directory not created")
            wf_dir = wf_dirs[0] / "walkforward"
            metrics_file = wf_dir / "metrics.csv"
            summary_file = wf_dir / "summary.json"
            self.assertTrue(metrics_file.exists(), "metrics.csv missing")
            self.assertTrue(summary_file.exists(), "summary.json missing")

            summary = json.loads(summary_file.read_text(encoding="utf-8"))
            self.assertEqual(summary.get("windows"), 2)
            self.assertIn("aggregates", summary)


if __name__ == "__main__":
    unittest.main()
