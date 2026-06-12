#!/usr/bin/env python3
"""Benchmark OCR model calls against categorized image folders."""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import html
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parents[3]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


_recognize_image = None


def get_recognize_image():
    global _recognize_image  # pylint: disable=global-statement
    if _recognize_image is not None:
        return _recognize_image
    module_path = Path(__file__).with_name("image_text_extractor_llm.py")
    spec = importlib.util.spec_from_file_location("wordfit_image_text_extractor_llm", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load OCR module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _recognize_image = module.recognize_image
    return _recognize_image


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}


@dataclass
class OcrImageResult:
    model: str
    category: str
    image_name: str
    image_index: int
    concurrency: int
    round_index: int
    request_index: int
    success: bool
    latency_ms: int
    latency_seconds: float
    output_chars: int
    error_type: str
    error_message: str


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in str(value or "").strip())[:120]


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = math.ceil((p / 100) * len(ordered)) - 1
    return ordered[min(max(idx, 0), len(ordered) - 1)]


def group_by(items: list[Any], key_func) -> dict[Any, list[Any]]:
    grouped: dict[Any, list[Any]] = {}
    for item in items:
        grouped.setdefault(key_func(item), []).append(item)
    return grouped


def discover_images(base_dir: Path) -> dict[str, list[Path]]:
    categories: dict[str, list[Path]] = {}
    for child in sorted(base_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        images = [path for path in sorted(child.iterdir(), key=lambda item: item.name.lower()) if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS]
        if images:
            categories[child.name] = images
    if not categories:
        raise ValueError(f"no image categories found under {base_dir}")
    return categories


async def run_one(model: str, category: str, image_path: Path, image_index: int, concurrency: int, round_index: int, request_index: int) -> OcrImageResult:
    started = time.perf_counter()
    try:
        recognize_image = get_recognize_image()
        result = await asyncio.to_thread(recognize_image, str(image_path), True, None, None, model)
        latency_ms = int((time.perf_counter() - started) * 1000)
        text = str(result.get("text_content") or "") if isinstance(result, dict) else ""
        success = bool(text.strip()) and not text.startswith("OCR识别失败") and not text.startswith("错误：")
        return OcrImageResult(model, category, image_path.name, image_index, concurrency, round_index, request_index, success, latency_ms, round(latency_ms / 1000, 2), len(text), "" if success else "ocr_empty_or_failed", "" if success else text[:300])
    except Exception as exc:  # pylint: disable=broad-except
        latency_ms = int((time.perf_counter() - started) * 1000)
        return OcrImageResult(model, category, image_path.name, image_index, concurrency, round_index, request_index, False, latency_ms, round(latency_ms / 1000, 2), 0, exc.__class__.__name__, str(exc)[:300])


async def run_batch(model: str, categories: dict[str, list[Path]], concurrency_values: list[int], rounds: int) -> list[OcrImageResult]:
    results: list[OcrImageResult] = []
    for concurrency in concurrency_values:
        for round_index in range(1, rounds + 1):
            tasks = []
            request_index = 0
            for category, images in categories.items():
                category_tasks = [(category, image_path, image_index) for image_index, image_path in enumerate(images, start=1)]
                while category_tasks and len(category_tasks) < concurrency:
                    category_tasks.extend(category_tasks[: concurrency - len(category_tasks)])
                for category_name, image_path, image_index in category_tasks:
                    request_index += 1
                    tasks.append((category_name, image_path, image_index, request_index))
            for start in range(0, len(tasks), concurrency):
                chunk = tasks[start : start + concurrency]
                pending = [run_one(model, category, image_path, image_index, concurrency, round_index, request_index) for category, image_path, image_index, request_index in chunk]
                chunk_results = await asyncio.gather(*pending)
                results.extend(chunk_results)
                print(f"[{now_text()}] model={model} concurrency={concurrency} round={round_index}/{rounds} done={min(start + len(chunk), len(tasks))}/{len(tasks)}")
    return results


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def summarize(results: list[OcrImageResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (model, category, concurrency), items in sorted(group_by(results, lambda item: (item.model, item.category, item.concurrency)).items()):
        latencies = [item.latency_ms for item in items if item.success]
        rows.append(
            {
                "model": model,
                "category": category,
                "concurrency": concurrency,
                "request_count": len(items),
                "success_count": sum(1 for item in items if item.success),
                "success_rate": round(sum(1 for item in items if item.success) / len(items), 4) if items else 0,
                "avg_seconds": round((statistics.mean(latencies) / 1000), 2) if latencies else 0,
                "min_seconds": round((min(latencies) / 1000), 2) if latencies else 0,
                "max_seconds": round((max(latencies) / 1000), 2) if latencies else 0,
                "p50_seconds": round((percentile(latencies, 50) / 1000), 2) if latencies else 0,
                "p90_seconds": round((percentile(latencies, 90) / 1000), 2) if latencies else 0,
                "p95_seconds": round((percentile(latencies, 95) / 1000), 2) if latencies else 0,
                "p99_seconds": round((percentile(latencies, 99) / 1000), 2) if latencies else 0,
                "avg_output_chars": int(statistics.mean([item.output_chars for item in items if item.success])) if any(item.success for item in items) else 0,
            }
        )
    return rows


def color_for_value(value: float, max_value: float) -> str:
    if max_value <= 0 or value <= 0:
        return "#f8fafc"
    ratio = min(max(value / max_value, 0), 1)
    red = int(230 - 45 * ratio)
    green = int(244 - 140 * ratio)
    blue = int(255 - 180 * ratio)
    return f"#{red:02x}{green:02x}{blue:02x}"


def write_heatmap_svg(path: Path, title: str, x_labels: list[str], y_labels: list[str], values: list[list[float]]) -> None:
    cell_w = 70
    cell_h = 34
    left = 92
    top = 58
    width = left + 28 + cell_w * max(len(x_labels), 1)
    height = top + 32 + cell_h * max(len(y_labels), 1)
    max_value = max([max(row) for row in values if row] or [0])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Arial,"Microsoft YaHei",sans-serif;font-size:12px;fill:#1f2937}.title{font-size:18px;font-weight:700}.axis{fill:#4b5563}.cell-text{font-size:11px}</style>',
        f'<rect width="{width}" height="{height}" fill="#fff"/>',
        f'<text class="title" x="20" y="28">{html.escape(title)}</text>',
    ]
    for col, label in enumerate(x_labels):
        x = left + col * cell_w + cell_w / 2
        parts.append(f'<text class="axis" x="{x}" y="{top - 14}" text-anchor="middle">{html.escape(label)}</text>')
    for row_index, label in enumerate(y_labels):
        y = top + row_index * cell_h + cell_h / 2 + 4
        parts.append(f'<text class="axis" x="{left - 12}" y="{y}" text-anchor="end">{html.escape(label)}</text>')
    for row_index, row in enumerate(values):
        for col, value in enumerate(row):
            x = left + col * cell_w
            y = top + row_index * cell_h
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" fill="{color_for_value(value, max_value)}" stroke="#d1d5db"/>')
            parts.append(f'<text class="cell-text" x="{x + cell_w / 2}" y="{y + cell_h / 2 + 4}" text-anchor="middle">{value:.2f}s</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_line_svg(path: Path, title: str, x_labels: list[str], series: list[tuple[str, list[float]]]) -> None:
    width = 880
    height = 360
    left = 64
    top = 48
    right = 180
    bottom = 54
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_value = max([max(values) for _name, values in series if values] or [1])
    max_value = max(max_value, 1)
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2"]

    def point(col: int, value: float) -> tuple[float, float]:
        x = left + (plot_w * col / max(len(x_labels) - 1, 1))
        y = top + plot_h - (plot_h * value / max_value)
        return x, y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Arial,"Microsoft YaHei",sans-serif;font-size:12px;fill:#1f2937}.title{font-size:18px;font-weight:700}.axis{fill:#4b5563}</style>',
        f'<rect width="{width}" height="{height}" fill="#fff"/>',
        f'<text class="title" x="20" y="28">{html.escape(title)}</text>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#9ca3af"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#9ca3af"/>',
    ]
    for idx, label in enumerate(x_labels):
        x, _ = point(idx, 0)
        parts.append(f'<text class="axis" x="{x}" y="{height - 22}" text-anchor="middle">{html.escape(label)}</text>')
    for tick in range(5):
        value = max_value * tick / 4
        y = top + plot_h - (plot_h * tick / 4)
        parts.append(f'<line x1="{left}" y1="{y}" x2="{left + plot_w}" y2="{y}" stroke="#e5e7eb"/>')
        parts.append(f'<text class="axis" x="{left - 8}" y="{y + 4}" text-anchor="end">{value:.2f}s</text>')
    for series_index, (name, values) in enumerate(series):
        color = colors[series_index % len(colors)]
        points = [point(idx, value) for idx, value in enumerate(values)]
        path_d = " ".join(("M" if idx == 0 else "L") + f"{x:.1f},{y:.1f}" for idx, (x, y) in enumerate(points))
        parts.append(f'<path d="{path_d}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y in points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
        legend_y = top + 18 + series_index * 20
        parts.append(f'<line x1="{left + plot_w + 24}" y1="{legend_y - 4}" x2="{left + plot_w + 44}" y2="{legend_y - 4}" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<text x="{left + plot_w + 50}" y="{legend_y}">{html.escape(name)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def generate_charts(output_dir: Path, results: list[OcrImageResult]) -> list[str]:
    chart_dir = ensure_dir(output_dir / "charts")
    matrix_dir = ensure_dir(output_dir / "matrix")
    chart_paths: list[str] = []
    for model in sorted({item.model for item in results}):
        model_results = [item for item in results if item.model == model]
        for category in sorted({item.category for item in model_results}):
            category_results = [item for item in model_results if item.category == category and item.success]
            if not category_results:
                continue
            concurrency_labels = [str(value) for value in sorted({item.concurrency for item in category_results})]
            image_labels = [f"p{value:02d}" for value in sorted({item.image_index for item in category_results})]
            grouped = group_by(category_results, lambda item: (item.concurrency, item.image_index))
            matrix: list[list[float]] = []
            with open(matrix_dir / f"{safe_name(model)}_{safe_name(category)}_heatmap_matrix.csv", "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["concurrency", *image_labels])
                for concurrency in sorted({item.concurrency for item in category_results}):
                    row = []
                    for image_index in sorted({item.image_index for item in category_results}):
                        values = [item.latency_seconds for item in grouped.get((concurrency, image_index), [])]
                        row.append(round(statistics.mean(values), 2) if values else 0)
                    matrix.append(row)
                    writer.writerow([concurrency, *row])
            heatmap = chart_dir / f"{safe_name(model)}_{safe_name(category)}_latency_heatmap.svg"
            write_heatmap_svg(heatmap, f"{model} {category} OCR latency", image_labels, [f"c{label}" for label in concurrency_labels], matrix)
            chart_paths.append(heatmap.relative_to(output_dir).as_posix())
        x_labels = [str(value) for value in sorted({item.concurrency for item in model_results})]
        series: list[tuple[str, list[float]]] = []
        for category in sorted({item.category for item in model_results}):
            values = []
            for concurrency in sorted({item.concurrency for item in model_results}):
                items = [item.latency_seconds for item in model_results if item.category == category and item.concurrency == concurrency and item.success]
                values.append(round(statistics.mean(items), 2) if items else 0)
            series.append((category, values))
        line = chart_dir / f"{safe_name(model)}_category_avg_latency_lines.svg"
        write_line_svg(line, f"{model} category average latency", x_labels, series)
        chart_paths.append(line.relative_to(output_dir).as_posix())
    return chart_paths


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_无数据_"
    lines = ["| " + " | ".join(title for title, _key in columns) + " |", "| " + " | ".join("---" for _title, _key in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(key, "")) for _title, key in columns) + " |")
    return "\n".join(lines)


def write_report(output_dir: Path, args: argparse.Namespace, summary_rows: list[dict[str, Any]], chart_paths: list[str]) -> None:
    content = [
        "# OCR 图片目录压测报告",
        "",
        f"- 生成时间：`{now_text()}`",
        f"- image_dir：`{args.image_dir}`",
        f"- ocr_models：`{args.ocr_models}`",
        f"- concurrency：`{','.join(str(item) for item in args.concurrency)}`",
        f"- rounds：`{args.rounds}`",
        f"- force：`true`",
        "",
        "## 汇总",
        "",
        markdown_table(
            summary_rows,
            [
                ("模型", "model"),
                ("类别", "category"),
                ("并发", "concurrency"),
                ("请求数", "request_count"),
                ("成功率", "success_rate"),
                ("平均秒", "avg_seconds"),
                ("最小秒", "min_seconds"),
                ("最大秒", "max_seconds"),
                ("P50秒", "p50_seconds"),
                ("P90秒", "p90_seconds"),
                ("P95秒", "p95_seconds"),
                ("P99秒", "p99_seconds"),
                ("平均输出字数", "avg_output_chars"),
            ],
        ),
        "",
        "## 图表",
        "",
    ]
    for chart_path in chart_paths:
        content.extend([f"![{Path(chart_path).stem}]({chart_path})", ""])
    content.extend(
        [
            "## 输出文件",
            "",
            "- `raw_results.csv`：每张图片每次 OCR 请求明细",
            "- `summary_by_category_concurrency.csv`：按模型、类别、并发汇总",
            "- `charts/`：SVG 热力图和折线图",
            "- `matrix/`：热力图矩阵 CSV",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(content) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark OCR model calls against categorized image folders.")
    parser.add_argument("--image-dir", required=True, help="base directory containing category subfolders")
    parser.add_argument("--ocr-models", required=True, help="comma-separated OCR model names")
    parser.add_argument("--output-dir", required=True, help="output directory")
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1], help="concurrency list")
    parser.add_argument("--rounds", type=int, default=1, help="rounds per concurrency")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = Path(args.image_dir).expanduser().resolve()
    output_dir = ensure_dir(args.output_dir)
    categories = discover_images(base_dir)
    models = [item.strip() for item in str(args.ocr_models).split(",") if item.strip()]
    if not models:
        raise ValueError("--ocr-models must contain at least one model")

    all_results: list[OcrImageResult] = []
    for model in models:
        print(f"[{now_text()}] benchmark start model={model} categories={len(categories)}")
        all_results.extend(asyncio.run(run_batch(model, categories, args.concurrency, args.rounds)))
    raw_rows = [item.__dict__ for item in all_results]
    summary_rows = summarize(all_results)
    write_csv(output_dir / "raw_results.csv", raw_rows, ["model", "category", "image_name", "image_index", "concurrency", "round_index", "request_index", "success", "latency_ms", "latency_seconds", "output_chars", "error_type", "error_message"])
    write_csv(output_dir / "summary_by_category_concurrency.csv", summary_rows, ["model", "category", "concurrency", "request_count", "success_count", "success_rate", "avg_seconds", "min_seconds", "max_seconds", "p50_seconds", "p90_seconds", "p95_seconds", "p99_seconds", "avg_output_chars"])
    chart_paths = generate_charts(output_dir, all_results)
    write_report(output_dir, args, summary_rows, chart_paths)
    print(f"[{now_text()}] benchmark done output_dir={output_dir}")


if __name__ == "__main__":
    main()
