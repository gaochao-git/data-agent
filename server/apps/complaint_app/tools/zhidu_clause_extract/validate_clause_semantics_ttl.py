from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from rdflib import Graph, Namespace
    from rdflib.namespace import RDF
except Exception:  # pragma: no cover - dependency guard
    Graph = None
    Namespace = None
    RDF = None


BASE_URI = "http://example.com/zhidu-clause-semantics/"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SHAPES_FILE = SCRIPT_DIR / "clause_semantics_shapes.ttl"
SH = Namespace("http://www.w3.org/ns/shacl#") if Namespace is not None else None


# 解析命令行参数。
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验制度条款语义 TTL 是否满足 SHACL 结构约束。")
    parser.add_argument("--ttl", required=True, help="待校验的 clause_semantics_ontology.ttl 路径")
    parser.add_argument("--shapes", default=str(DEFAULT_SHAPES_FILE), help="SHACL shapes TTL 路径")
    parser.add_argument("--output-dir", default="", help="报告输出目录，默认与 TTL 同目录")
    parser.add_argument("--base-uri", default=BASE_URI, help="解析相对 IRI 时使用的 base URI")
    parser.add_argument("--no-fail-on-violation", action="store_true", help="发现违规时仍返回 0")
    return parser


# 加载 Turtle 图。
def load_turtle_graph(path: Path, base_uri: str) -> Any:
    if Graph is None:
        raise ModuleNotFoundError("缺少 rdflib 依赖，请先安装：pip install rdflib")
    graph = Graph()
    graph.parse(path.as_posix(), format="turtle", publicID=base_uri)
    return graph


# 调用 pyshacl 执行约束校验。
def run_shacl_validation(data_graph: Any, shapes_graph: Any) -> tuple[bool, Any, str]:
    try:
        from pyshacl import validate
    except Exception as err:
        raise ModuleNotFoundError("缺少 pyshacl 依赖，请先安装：pip install pyshacl") from err
    conforms, report_graph, report_text = validate(data_graph=data_graph, shacl_graph=shapes_graph, inference="rdfs", abort_on_first=False, allow_infos=True, allow_warnings=True)
    return bool(conforms), report_graph, str(report_text or "")


# 提取 RDF 节点展示值。
def node_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


# 将 SHACL report graph 转成 JSON 友好结构。
def build_report_json(conforms: bool, report_graph: Any, report_text: str) -> dict[str, Any]:
    if RDF is None or SH is None:
        raise ModuleNotFoundError("缺少 rdflib 依赖，请先安装：pip install rdflib")
    results: list[dict[str, str]] = []
    for result in report_graph.subjects(RDF.type, SH.ValidationResult):
        messages = [node_text(item) for item in report_graph.objects(result, SH.resultMessage)]
        results.append(
            {
                "focus_node": node_text(next(report_graph.objects(result, SH.focusNode), None)),
                "result_path": node_text(next(report_graph.objects(result, SH.resultPath), None)),
                "value_node": node_text(next(report_graph.objects(result, SH.value), None)),
                "source_shape": node_text(next(report_graph.objects(result, SH.sourceShape), None)),
                "source_constraint": node_text(next(report_graph.objects(result, SH.sourceConstraintComponent), None)),
                "severity": node_text(next(report_graph.objects(result, SH.resultSeverity), None)),
                "message": " | ".join([message for message in messages if message]),
            }
        )
    return {
        "conforms": conforms,
        "violation_count": len(results),
        "results": results,
        "report_text": report_text,
    }


# 写 JSON 文件。
def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# 写文本文件。
def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    ttl_path = Path(args.ttl).expanduser().resolve()
    shapes_path = Path(args.shapes).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if str(args.output_dir or "").strip() else ttl_path.parent
    data_graph = load_turtle_graph(ttl_path, str(args.base_uri or BASE_URI))
    shapes_graph = load_turtle_graph(shapes_path, str(args.base_uri or BASE_URI))
    conforms, report_graph, report_text = run_shacl_validation(data_graph, shapes_graph)
    report_json = build_report_json(conforms, report_graph, report_text)
    json_path = output_dir / "shacl_validation_report.json"
    text_path = output_dir / "shacl_validation_report.txt"
    write_json(json_path, report_json)
    write_text(text_path, report_text)
    print(f"shacl report json: {json_path}")
    print(f"shacl report text: {text_path}")
    print(f"conforms={str(conforms).lower()} violation_count={report_json['violation_count']}")
    if conforms or bool(args.no_fail_on_violation):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
