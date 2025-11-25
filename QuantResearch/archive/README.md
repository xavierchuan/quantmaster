## Archive Staging Area

This directory stores compressed copies of large artifacts (results, models, stats) that are no longer needed in the working tree but must remain reproducible.

**Rules**
- Place deprecated-but-referenceable source files under the relevant subfolder (`scripts/`, `strategies/`, `core/`). Tarballs of large artifacts can still live here, but keep them gitignored.
- Update `docs/retention.md` whenever you add/remove an archive so reviewers know where to find it.
- When restoring an archived script/strategy, move it back to its original location (or re-implement properly) and note the change in the retention doc/PR.
- When restoring artifacts (results tarballs, models, etc.), extract them under the original path (e.g., `QuantResearch/results/<run_id>`), refresh `summary.json.retention`, and rerun any manifests that depend on the resurrected files.
