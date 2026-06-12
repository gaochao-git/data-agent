from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SERVER_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SPARQL_FILE = SCRIPT_DIR / "examples" / "conflict_candidates.sparql"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="编排执行条款语义 04-09 流程。")
    parser.add_argument("--output-root", required=True, help="批次 output 目录，如 /data/aigc/zhidu_0430/output")
    parser.add_argument("--model", default="GLM-4.7-W8A8", help="模型名，默认 GLM-4.7-W8A8")
    parser.add_argument("--start-step", choices=["04", "05", "06", "06b", "07", "08", "graphdb", "09"], default="04", help="从哪一步开始执行")
    parser.add_argument("--end-step", choices=["04", "05", "06", "06b", "07", "08", "graphdb", "09"], default="09", help="执行到哪一步结束")
    parser.add_argument("--graphdb-repository", default="", help="GraphDB repository 查询地址，如 http://172.27.60.34:7200/repositories/zhidu")
    parser.add_argument("--sparql-file", default=str(DEFAULT_SPARQL_FILE), help="冲突候选 SPARQL 文件")
    parser.add_argument("--concurrency", type=int, default=3, help="传给支持并发的步骤，默认 3")
    parser.add_argument("--page-size", type=int, default=20, help="09 步骤 SPARQL 每页取多少候选，默认 20")
    parser.add_argument("--timeout-seconds", type=int, default=30, help="09 步骤 SPARQL 请求超时秒数，默认 30")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不执行")
    return parser


def script_path(name: str) -> str:
    return str(SCRIPT_DIR / name)


def ensure_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} 不存在: {path}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def ensure_file_if_running(path: Path, label: str, dry_run: bool) -> None:
    if dry_run:
        return
    ensure_file(path, label)


def ensure_dir_if_running(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    ensure_dir(path)


def run_command(command: list[str], dry_run: bool) -> None:
    print("+ " + " ".join(command), flush=True)
    if dry_run:
        return
    subprocess.run(command, cwd=str(SERVER_ROOT), check=True)


def selected_steps(start_step: str, end_step: str) -> list[str]:
    steps = ["04", "05", "06", "06b", "07", "08", "graphdb", "09"]
    start_index = steps.index(start_step)
    end_index = steps.index(end_step)
    if start_index > end_index:
        raise ValueError(f"start-step 不能晚于 end-step: {start_step} > {end_step}")
    return steps[start_index:end_index + 1]


def build_paths(output_root: Path) -> dict[str, Path]:
    return {
        "clauses_jsonl": output_root / "01_extract" / "clauses.jsonl",
        "documents_mapped_jsonl": output_root / "02_outline_mapping" / "documents_mapped.jsonl",
        "mentions_jsonl": output_root / "04_clause_semantics_mentions" / "clause_semantics_mentions.jsonl",
        "normalization_jsonl": output_root / "05_clause_semantics_normalization" / "clause_semantics_normalization.jsonl",
        "concepts_jsonl": output_root / "06_clause_semantics_concepts" / "clause_semantics_concepts.jsonl",
        "terms_jsonl": output_root / "06b_clause_semantics_terms" / "clause_semantics_terms.jsonl",
        "rule_expressions_jsonl": output_root / "07_clause_rule_expressions" / "clause_rule_expressions.jsonl",
        "graphdb_ttl": output_root / "08_clause_semantics_ontology" / "clause_semantics_ontology.graphdb_ready.ttl",
    }


def run_step_04(paths: dict[str, Path], output_root: Path, model: str, concurrency: int, dry_run: bool) -> None:
    ensure_file_if_running(paths["clauses_jsonl"], "clauses.jsonl", dry_run)
    ensure_dir_if_running(output_root / "04_clause_semantics_mentions", dry_run)
    run_command(
        [
            sys.executable,
            script_path("extract_clause_semantics_mentions.py"),
            "--clauses-jsonl",
            str(paths["clauses_jsonl"]),
            "--output-dir",
            str(output_root / "04_clause_semantics_mentions"),
            "--model",
            model,
            "--concurrency",
            str(concurrency),
        ],
        dry_run,
    )


def run_step_05(paths: dict[str, Path], output_root: Path, model: str, dry_run: bool) -> None:
    ensure_file_if_running(paths["mentions_jsonl"], "clause_semantics_mentions.jsonl", dry_run)
    ensure_dir_if_running(output_root / "05_clause_semantics_normalization", dry_run)
    run_command(
        [
            sys.executable,
            script_path("normalize_clause_semantics_mentions.py"),
            "--mentions-jsonl",
            str(paths["mentions_jsonl"]),
            "--output-dir",
            str(output_root / "05_clause_semantics_normalization"),
            "--max-doc-mentions-per-batch",
            "120",
            "--model",
            model,
        ],
        dry_run,
    )


def run_step_06(paths: dict[str, Path], output_root: Path, model: str, concurrency: int, dry_run: bool) -> None:
    ensure_file_if_running(paths["normalization_jsonl"], "clause_semantics_normalization.jsonl", dry_run)
    ensure_dir_if_running(output_root / "06_clause_semantics_concepts", dry_run)
    run_command(
        [
            sys.executable,
            script_path("screen_clause_semantics_concepts.py"),
            "--normalization-jsonl",
            str(paths["normalization_jsonl"]),
            "--output-dir",
            str(output_root / "06_clause_semantics_concepts"),
            "--model",
            model,
            "--concurrency",
            str(concurrency),
        ],
        dry_run,
    )


def run_step_06b(paths: dict[str, Path], output_root: Path, model: str, concurrency: int, dry_run: bool) -> None:
    ensure_file_if_running(paths["normalization_jsonl"], "clause_semantics_normalization.jsonl", dry_run)
    ensure_file_if_running(paths["concepts_jsonl"], "clause_semantics_concepts.jsonl", dry_run)
    ensure_dir_if_running(output_root / "06b_clause_semantics_terms", dry_run)
    run_command(
        [
            sys.executable,
            script_path("enrich_clause_semantics_terms.py"),
            "--normalization-jsonl",
            str(paths["normalization_jsonl"]),
            "--concepts-jsonl",
            str(paths["concepts_jsonl"]),
            "--output-dir",
            str(output_root / "06b_clause_semantics_terms"),
            "--model",
            model,
            "--concurrency",
            str(concurrency),
        ],
        dry_run,
    )


def run_step_07(paths: dict[str, Path], output_root: Path, model: str, concurrency: int, dry_run: bool) -> None:
    ensure_file_if_running(paths["mentions_jsonl"], "clause_semantics_mentions.jsonl", dry_run)
    ensure_file_if_running(paths["normalization_jsonl"], "clause_semantics_normalization.jsonl", dry_run)
    ensure_file_if_running(paths["concepts_jsonl"], "clause_semantics_concepts.jsonl", dry_run)
    ensure_dir_if_running(output_root / "07_clause_rule_expressions", dry_run)
    run_command(
        [
            sys.executable,
            script_path("extract_clause_rule_expressions.py"),
            "--mentions-jsonl",
            str(paths["mentions_jsonl"]),
            "--normalization-jsonl",
            str(paths["normalization_jsonl"]),
            "--concepts-jsonl",
            str(paths["concepts_jsonl"]),
            "--output-dir",
            str(output_root / "07_clause_rule_expressions"),
            "--model",
            model,
            "--concurrency",
            str(concurrency),
        ],
        dry_run,
    )


def run_step_08(paths: dict[str, Path], output_root: Path, model: str, concurrency: int, dry_run: bool) -> None:
    ensure_file_if_running(paths["documents_mapped_jsonl"], "documents_mapped.jsonl", dry_run)
    ensure_file_if_running(paths["normalization_jsonl"], "clause_semantics_normalization.jsonl", dry_run)
    ensure_file_if_running(paths["concepts_jsonl"], "clause_semantics_concepts.jsonl", dry_run)
    ensure_file_if_running(paths["terms_jsonl"], "clause_semantics_terms.jsonl", dry_run)
    ensure_file_if_running(paths["rule_expressions_jsonl"], "clause_rule_expressions.jsonl", dry_run)
    ensure_dir_if_running(output_root / "08_clause_semantics_ontology", dry_run)
    run_command(
        [
            sys.executable,
            script_path("build_clause_semantics_ontology_ttl.py"),
            "--documents-jsonl",
            str(paths["documents_mapped_jsonl"]),
            "--normalization-jsonl",
            str(paths["normalization_jsonl"]),
            "--concepts-jsonl",
            str(paths["concepts_jsonl"]),
            "--terms-jsonl",
            str(paths["terms_jsonl"]),
            "--rule-expressions-jsonl",
            str(paths["rule_expressions_jsonl"]),
            "--output-dir",
            str(output_root / "08_clause_semantics_ontology"),
            "--model",
            model,
            "--concurrency",
            str(concurrency),
        ],
        dry_run,
    )


def run_step_graphdb(paths: dict[str, Path], graphdb_repository: str, dry_run: bool) -> None:
    if not graphdb_repository:
        raise ValueError("执行 graphdb 步骤必须传 --graphdb-repository")
    ensure_file_if_running(paths["graphdb_ttl"], "clause_semantics_ontology.graphdb_ready.ttl", dry_run)
    statements_url = graphdb_repository.rstrip("/") + "/statements"
    run_command(
        [
            "curl",
            "-X",
            "POST",
            "-H",
            "Content-Type: text/turtle",
            "--data-binary",
            f"@{paths['graphdb_ttl']}",
            statements_url,
        ],
        dry_run,
    )


def run_step_09(output_root: Path, graphdb_repository: str, sparql_file: Path, model: str, page_size: int, timeout_seconds: int, dry_run: bool) -> None:
    if not graphdb_repository:
        raise ValueError("执行 09 步骤必须传 --graphdb-repository")
    ensure_file_if_running(sparql_file, "conflict_candidates.sparql", dry_run)
    ensure_dir_if_running(output_root / "09_ontology_conflict_judgments", dry_run)
    run_command(
        [
            sys.executable,
            script_path("judge_ontology_conflicts.py"),
            "--sparql-endpoint",
            graphdb_repository,
            "--sparql-file",
            str(sparql_file),
            "--output-dir",
            str(output_root / "09_ontology_conflict_judgments"),
            "--model",
            model,
            "--page-size",
            str(page_size),
            "--timeout-seconds",
            str(timeout_seconds),
        ],
        dry_run,
    )


def main() -> int:
    args = build_parser().parse_args()
    output_root = Path(args.output_root).expanduser().resolve()
    paths = build_paths(output_root)
    steps = selected_steps(str(args.start_step), str(args.end_step))
    for step in steps:
        print(f"\n== step {step} ==", flush=True)
        if step == "04":
            run_step_04(paths, output_root, str(args.model), int(args.concurrency), bool(args.dry_run))
        elif step == "05":
            run_step_05(paths, output_root, str(args.model), bool(args.dry_run))
        elif step == "06":
            run_step_06(paths, output_root, str(args.model), int(args.concurrency), bool(args.dry_run))
        elif step == "06b":
            run_step_06b(paths, output_root, str(args.model), int(args.concurrency), bool(args.dry_run))
        elif step == "07":
            run_step_07(paths, output_root, str(args.model), int(args.concurrency), bool(args.dry_run))
        elif step == "08":
            run_step_08(paths, output_root, str(args.model), int(args.concurrency), bool(args.dry_run))
        elif step == "graphdb":
            run_step_graphdb(paths, str(args.graphdb_repository), bool(args.dry_run))
        elif step == "09":
            run_step_09(output_root, str(args.graphdb_repository), Path(args.sparql_file).expanduser().resolve(), str(args.model), int(args.page_size), int(args.timeout_seconds), bool(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
