import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


BASE_DIR = Path(__file__).resolve().parents[1]


class PlotDiagnosticsCLITest(unittest.TestCase):
    def test_cli_outputs_charts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            cmd = [
                sys.executable,
                "scripts/plot_backtest_diagnostics.py",
                "--batch-csv",
                "tests/fixtures/batch_results_sample.csv",
                "--walkforward-csv",
                "tests/fixtures/walkforward_metrics_sample.csv",
                "--mc-summary",
                "tests/fixtures/mc_summary_sample.json",
                "--mc-iterations",
                "tests/fixtures/mc_iterations_sample.csv",
                "--equity-csv",
                "tests/fixtures/equity_sample.csv",
                "--underwater-csv",
                "tests/fixtures/underwater_sample.csv",
                "--out",
                str(out_dir),
                "--format",
                "png",
                "--facet-scenario",
            ]
            subprocess.run(cmd, cwd=BASE_DIR, check=True)

            diag_dirs = list(out_dir.glob("diagnostics_*/"))
            self.assertTrue(diag_dirs, "Diagnostics directory not created")
            produced = list(diag_dirs[0].glob("*.png"))
            self.assertGreaterEqual(len(produced), 4, "Expected multiple charts")
            meta = diag_dirs[0] / "diagnostics_metadata.json"
            data_json = diag_dirs[0] / "diagnostics_data.json"
            self.assertTrue(meta.exists())
            self.assertTrue(data_json.exists())
            meta_data = json.loads(meta.read_text(encoding="utf-8"))
            self.assertIn("batch_heatmap", meta_data.get("artifacts", {}))
            self.assertIn("equity_curve", meta_data.get("artifacts", {}))
            data_dump = json.loads(data_json.read_text(encoding="utf-8"))
            self.assertIn("batch_heatmap", data_dump)


if __name__ == "__main__":
    unittest.main()
