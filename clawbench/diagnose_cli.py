"""ClawBench v0.5 — `clawbench-diagnose` CLI.

Usage:
    python -m clawbench.diagnose_cli <profile.yaml>
        [--db <path>]
        [--manifests <dir>]
        [--results <results.json>]
        [--transcripts <transcripts.json>]
        [--tier-map <tier_map.json>]
        [--insights-dir <dir>]
        [--no-record]
        [--json]

Without --results, the tool runs in PRE-RUN PREDICTION mode:
    - parses the profile
    - computes the fingerprint
    - looks up neighbors in the historical database
    - prints a predictive diagnostic (no actual scores yet)

With --results, the tool runs in POST-RUN ANALYSIS mode:
    - everything above
    - plus surprise detection against the actual results
    - plus robustness profile, plugin utilization audit,
      manifest-vs-reality gap, and recommendations (when transcripts given)
    - plus ecosystem insight files published to --insights-dir
    - plus appends the run to the historical database
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clawbench.diagnostic import build_diagnostic, submit_run
from clawbench.insights import publish_insights
from clawbench.prediction import HistoricalDatabase
from clawbench.profile import PluginManifest, PluginProfile, RegistrationTrace
from clawbench.schemas import ToolCall, Transcript
from clawbench.trajectory import classify_tool_call


DEFAULT_CLAWBENCH_ROOT = Path(".clawbench")
DEFAULT_DB_PATH = DEFAULT_CLAWBENCH_ROOT / "historical" / "profile_runs.json"
DEFAULT_MANIFEST_DIR = DEFAULT_CLAWBENCH_ROOT / "manifests"
DEFAULT_INSIGHTS_DIR = DEFAULT_CLAWBENCH_ROOT / "insights"
DEFAULT_SUBMISSIONS_DIR = DEFAULT_CLAWBENCH_ROOT / "submissions"


def ensure_data_dirs(root: Path = DEFAULT_CLAWBENCH_ROOT) -> None:
    """Create the v0.5 data model directories if they do not exist."""
    (root / "historical").mkdir(parents=True, exist_ok=True)
    (root / "manifests").mkdir(parents=True, exist_ok=True)
    (root / "insights").mkdir(parents=True, exist_ok=True)
    (root / "submissions").mkdir(parents=True, exist_ok=True)


def load_manifests(manifest_dir: Path, plugin_ids: list[str]) -> dict[str, PluginManifest]:
    out: dict[str, PluginManifest] = {}
    if not manifest_dir.exists():
        return out
    for pid in plugin_ids:
        candidate = manifest_dir / f"{pid}.json"
        if candidate.exists():
            out[pid] = PluginManifest.from_file(candidate)
    return out


def load_transcripts(path: Path) -> dict[str, Transcript]:
    """Load per-task transcripts from a JSON file.

    Expected shape: {"<task_id>": <transcript_dict>, ...}
    Each transcript_dict must be valid for `Transcript.model_validate`.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Transcript] = {}
    if isinstance(data, dict):
        for task_id, raw in data.items():
            out[str(task_id)] = Transcript.model_validate(raw)
    return out


def infer_registration_traces_from_manifests(
    profile: PluginProfile,
    manifests: dict[str, PluginManifest],
) -> dict[str, RegistrationTrace]:
    """Build best-effort registration traces from manifest-declared tools.

    Full runtime registration traces are better because they include hooks,
    gateway methods, routes, and services. This fallback still gives the
    diagnostic layer exact manifest-declared tool names, which is enough to
    attribute many transcript tool calls instead of dropping all utilization
    into the unassigned bucket.
    """
    traces: dict[str, RegistrationTrace] = {}
    for entry in profile.plugins:
        manifest = manifests.get(entry.id)
        if manifest is None:
            continue
        tools = list(manifest.contracts.get("tools", []))
        families = sorted(
            {
                classify_tool_call(ToolCall(name=tool))[0]
                for tool in tools
                if tool
            }
        )
        traces[entry.id] = RegistrationTrace(
            plugin_id=entry.id,
            tools=tools,
            tool_families_seen=families,
        )
    return traces


def write_submission_record(
    submissions_dir: Path, fingerprint_hash: str, report_dict: dict
) -> Path:
    submissions_dir.mkdir(parents=True, exist_ok=True)
    path = submissions_dir / f"{fingerprint_hash}.json"
    path.write_text(json.dumps(report_dict, indent=2, default=str), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ClawBench v0.5 plugin profile diagnostic"
    )
    parser.add_argument("profile", type=Path, help="Path to profile YAML")
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to historical database JSON",
    )
    parser.add_argument(
        "--manifests",
        type=Path,
        default=DEFAULT_MANIFEST_DIR,
        help="Directory containing plugin manifest JSON files",
    )
    parser.add_argument(
        "--insights-dir",
        type=Path,
        default=DEFAULT_INSIGHTS_DIR,
        help="Directory to write ecosystem insight files to after a post-run analysis",
    )
    parser.add_argument(
        "--submissions-dir",
        type=Path,
        default=DEFAULT_SUBMISSIONS_DIR,
        help="Directory to write per-submission diagnostic JSON files to",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=None,
        help="Optional path to actual benchmark results JSON; enables post-run mode",
    )
    parser.add_argument(
        "--transcripts",
        type=Path,
        default=None,
        help="Optional path to per-task transcripts JSON (enables utilization audit)",
    )
    parser.add_argument(
        "--tier-map",
        type=Path,
        default=None,
        help="Optional path to {task_id: tier} JSON map for per-tier robustness",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="Don't record this run in the historical database",
    )
    parser.add_argument(
        "--no-publish-insights",
        action="store_true",
        help="Don't write ecosystem insight files after a post-run analysis",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of text",
    )
    args = parser.parse_args()

    if not args.profile.exists():
        print(f"error: profile {args.profile} does not exist", file=sys.stderr)
        sys.exit(2)

    ensure_data_dirs()

    profile = PluginProfile.from_yaml_file(args.profile)
    plugin_ids = [e.id for e in profile.plugins]
    manifests = load_manifests(args.manifests, plugin_ids)
    traces = infer_registration_traces_from_manifests(profile, manifests)
    db = HistoricalDatabase(path=args.db)

    actual_overall: float | None = None
    actual_per_task: dict[str, float] | None = None
    if args.results:
        if not args.results.exists():
            print(f"error: results file {args.results} does not exist", file=sys.stderr)
            sys.exit(2)
        results_data = json.loads(args.results.read_text(encoding="utf-8"))
        actual_overall = float(results_data.get("overall_score", 0.0))
        if "per_task_score" in results_data:
            actual_per_task = {
                k: float(v) for k, v in results_data.get("per_task_score", {}).items()
            }
        else:
            actual_per_task = {
                str(item.get("task_id")): float(item.get("mean_task_score", 0.0))
                for item in results_data.get("task_results", [])
                if item.get("task_id")
            }

    transcripts: dict[str, Transcript] | None = None
    if args.transcripts:
        if not args.transcripts.exists():
            print(
                f"error: transcripts file {args.transcripts} does not exist",
                file=sys.stderr,
            )
            sys.exit(2)
        transcripts = load_transcripts(args.transcripts)

    tier_of: dict[str, str] | None = None
    if args.tier_map:
        if not args.tier_map.exists():
            print(
                f"error: tier map {args.tier_map} does not exist",
                file=sys.stderr,
            )
            sys.exit(2)
        tier_of = {
            str(k): str(v)
            for k, v in json.loads(
                args.tier_map.read_text(encoding="utf-8")
            ).items()
        }

    if args.results and not args.no_record and actual_per_task is not None and actual_overall is not None:
        report = submit_run(
            profile=profile,
            manifests=manifests,
            db=db,
            actual_overall_score=actual_overall,
            actual_per_task_scores=actual_per_task,
            traces=traces,
            transcripts=transcripts,
            tier_of=tier_of,
        )
        # Publish ecosystem insights after inserting the new run
        if not args.no_publish_insights:
            publish_insights(
                db, args.insights_dir, factor_report=report.factor_analysis
            )
    else:
        report = build_diagnostic(
            profile=profile,
            manifests=manifests,
            db=db,
            actual_overall_score=actual_overall,
            actual_per_task_scores=actual_per_task,
            traces=traces,
            transcripts=transcripts,
            tier_of=tier_of,
        )

    report_dict = report.to_dict()

    # Persist per-submission record
    write_submission_record(
        args.submissions_dir, report.fingerprint_hash, report_dict
    )

    if args.json:
        print(json.dumps(report_dict, indent=2, default=str))
    else:
        print(report.render_text())


if __name__ == "__main__":
    main()
