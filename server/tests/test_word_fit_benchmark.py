import importlib.util
import os
import sys
from argparse import Namespace
from pathlib import Path


def load_benchmark_module():
    module_path = Path(__file__).resolve().parents[1] / "apps" / "word_fit_app" / "tools" / "field_extract_benchmark.py"
    spec = importlib.util.spec_from_file_location("field_extract_benchmark", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flatten_and_lookup_nested_json_paths():
    benchmark = load_benchmark_module()
    payload = {"reporter": "张三", "complaints": [{"label": "违规开立支付账户"}]}

    flattened = benchmark.flatten_json(payload)

    assert flattened["reporter"] == "张三"
    assert flattened["complaints[0].label"] == "违规开立支付账户"
    assert benchmark.lookup_path(payload, "complaints[0].label") == "违规开立支付账户"


def test_long_text_defaults_to_llm_judge_and_can_skip_without_judge():
    benchmark = load_benchmark_module()
    gold_value = (
        "这是一个需要语义判断的较长字段，包含事件经过、关键诉求、引用法规以及多项事实描述，不能只靠完全匹配判断。"
        "例如举报事项、事实经过、监管依据和处理诉求可能存在同义表达，需要由评估模型判断语义是否一致。"
    )

    match_type = benchmark.infer_match_type(gold_value)
    judge_result, judge_reason = benchmark.compare_without_llm(match_type, gold_value, "实际抽取到的长文本")

    assert match_type == "llm_judge"
    assert judge_result == "skipped"
    assert judge_reason == "llm judge disabled"


def test_build_request_payload_can_use_full_payload_and_disable_analysis():
    benchmark = load_benchmark_module()
    args = Namespace(
        file_id="",
        extract_fields="",
        disable_analysis_preheat=True,
        locate_enabled=False,
        force=True,
        extract_model="qwen3.5",
        ocr_model="",
        ocr_metrics_batch_id="batch-1",
    )
    payload_template = {
        "file_id": "case-file-id",
        "extract_fields": [{"msgkey": "reporter", "description": "举报人"}],
        "analysis_scene": "complaint",
        "analysis_user_id": "user001",
        "analysis_match_strategy": "two_stage",
    }

    payload = benchmark.build_request_payload(args, payload_template, payload_template["extract_fields"], "bench-001")

    assert payload["file_id"] == "case-file-id"
    assert payload["conversation_id"] == "bench-001"
    assert payload["force"] is True
    assert payload["extract_model"] == "qwen3.5"
    assert payload["ocr_metrics_batch_id"] == "batch-1"
    assert payload["analysis_scene"] is None
    assert payload["analysis_user_id"] is None
    assert payload["analysis_match_strategy"] is None


def test_match_rules_can_ignore_field():
    benchmark = load_benchmark_module()
    gold_answer = {
        "institutions": ["机构A", "机构B", "机构C"],
        "summary": "自然语言概要，每次模型输出可能不同。",
        "reporter": "张三",
    }

    targets = benchmark.build_compare_targets(gold_answer, {"summary": "ignore", "institutions": "ignore"})

    assert "summary" not in targets
    assert "institutions" not in targets
    assert "institutions[0]" not in targets
    assert targets["reporter"] == "张三"


def test_match_rules_star_can_default_to_llm_judge(tmp_path):
    benchmark = load_benchmark_module()
    args = Namespace(output_dir=str(tmp_path), judge_mode="none")
    result = benchmark.RequestResult(
        request_id="req-1",
        model_label="qwen3.5",
        endpoint="http://example.com",
        file_id="case-file-id",
        concurrency=1,
        round_index=1,
        request_index=1,
        force=False,
        extract_model="qwen3.5",
        ocr_model="",
        success=True,
        latency_ms=100,
        http_status=200,
        json_valid=True,
        empty_result=False,
        extract={"reporter": "张 三"},
    )

    compares = benchmark.build_field_compares(args, tmp_path, [result], {"reporter": "张三"}, {"*": "llm_judge"}, set())

    assert compares[0].match_type == "llm_judge"
    assert compares[0].judge_result == "skipped"
    assert (tmp_path / "field_compare.csv").exists()


def test_extract_result_parses_markdown_json_string():
    benchmark = load_benchmark_module()
    events = [
        {
            "type": "result",
            "data": {
                "extract": '```json\n{"reporter": "张三", "amount": 100}\n```',
                "locate": {},
            },
        }
    ]

    extract, json_valid = benchmark.extract_result_from_events(events)

    assert json_valid is True
    assert extract == {"reporter": "张三", "amount": 100}


def test_resume_loads_extract_from_responses_jsonl(tmp_path):
    benchmark = load_benchmark_module()
    benchmark.append_request_results(
        tmp_path,
        [
            benchmark.RequestResult(
                request_id="qwen_c1_r001_req001",
                model_label="qwen",
                endpoint="http://example.com",
                file_id="case-file-id",
                concurrency=1,
                round_index=1,
                request_index=1,
                force=False,
                extract_model="qwen",
                ocr_model="",
                success=True,
                latency_ms=100,
                http_status=200,
                json_valid=True,
                empty_result=False,
                extract={"reporter": "张三"},
            )
        ],
    )

    results = benchmark.load_existing_request_results(tmp_path)

    assert results[0].request_id == "qwen_c1_r001_req001"
    assert results[0].extract == {"reporter": "张三"}


def test_load_env_file_sets_missing_env(monkeypatch, tmp_path):
    benchmark = load_benchmark_module()
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_BASE_URL=https://example.test/v1\nOPENAI_API_KEY='secret-key'\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = benchmark.load_env_file(str(env_path))

    assert loaded is True
    assert os.environ["OPENAI_BASE_URL"] == "https://example.test/v1"
    assert os.environ["OPENAI_API_KEY"] == "secret-key"


def test_filter_existing_compares_can_rerun_judge_errors():
    benchmark = load_benchmark_module()
    args = Namespace(rerun_judge=False, rerun_judge_errors=True)
    compares = [
        benchmark.FieldCompare("req-1", "qwen", 1, 1, "a", "llm_judge", "1", "1", "judge_error", "HTTP 422"),
        benchmark.FieldCompare("req-1", "qwen", 1, 1, "b", "llm_judge", "2", "2", "correct", "ok"),
        benchmark.FieldCompare("req-1", "qwen", 1, 1, "c", "exact", "3", "3", "correct", "ok"),
    ]

    filtered = benchmark.filter_existing_compares(args, compares)

    assert [(item.field_key, item.judge_result) for item in filtered] == [("b", "correct"), ("c", "correct")]


def test_parse_ocr_log_file_extracts_request_id_and_timings(tmp_path):
    benchmark = load_benchmark_module()
    log_path = tmp_path / "uat.log"
    log_path.write_text(
        "[OCR] 第 1 页处理完成 file=/data/uploads/runs/qwen_c1_r001_req001/demo.pdf page=1 started_at=2026-05-12 10:00:00.000 finished_at=2026-05-12 10:00:05.000 render_ms=300 ocr_ms=4700 duration_ms=5000 text_len=120 saved_path=/tmp/a.txt\n"
        "[timing] app=word_fit_app kind=ocr_file file=/data/uploads/runs/qwen_c1_r001_req001/demo.pdf duration_ms=5300 model=qwen thinking=False cache_hit=False mime_type=application/pdf pages=1 workers=1\n",
        encoding="utf-8",
    )

    details, timings = benchmark.parse_ocr_log_file(str(log_path))

    assert len(details) == 1
    assert details[0].request_id == "qwen_c1_r001_req001"
    assert details[0].render_ms == 300
    assert len(timings) == 1
    assert timings[0].request_id == "qwen_c1_r001_req001"
    assert timings[0].duration_ms == 5300


def test_parse_ocr_metrics_dir_extracts_timings(tmp_path):
    benchmark = load_benchmark_module()
    batch_dir = tmp_path / "batch-1"
    batch_dir.mkdir()
    metrics_path = batch_dir / "qwen_c1_r001_req001.jsonl"
    metrics_path.write_text(
        '{"type":"ocr_page","request_id":"qwen_c1_r001_req001","file_path":"/data/demo.pdf","page":1,"started_at":"2026-05-12 10:00:00.000","finished_at":"2026-05-12 10:00:05.000","render_ms":300,"ocr_ms":4700,"duration_ms":5000,"text_len":120,"saved_path":"/tmp/a.txt","record_type":"pdf_page"}\n'
        '{"type":"ocr_file","request_id":"qwen_c1_r001_req001","file_path":"/data/demo.pdf","duration_ms":5300,"model":"qwen","cache_hit":false,"mime_type":"application/pdf","pages":1,"workers":1}\n',
        encoding="utf-8",
    )

    details, timings = benchmark.parse_ocr_metrics_dir(str(tmp_path), "batch-1")

    assert len(details) == 1
    assert details[0].request_id == "qwen_c1_r001_req001"
    assert details[0].ocr_ms == 4700
    assert len(timings) == 1
    assert timings[0].duration_ms == 5300


def test_parse_llm_metrics_and_generate_charts(tmp_path):
    benchmark = load_benchmark_module()
    batch_dir = tmp_path / "batch-2"
    batch_dir.mkdir()
    metrics_path = batch_dir / "qwen_c1_r001_req001.jsonl"
    metrics_path.write_text(
        '{"type":"ocr_llm","request_id":"qwen_c1_r001_req001","page":1,"model":"qwen","prompt_chars":100,"output_chars":300,"first_token_ms":800,"llm_duration_ms":5000}\n'
        '{"type":"extract_llm","request_id":"qwen_c1_r001_req001","model":"qwen-extract","prompt_chars":2000,"output_chars":500,"first_token_ms":900,"llm_duration_ms":6000}\n',
        encoding="utf-8",
    )
    result = benchmark.RequestResult(
        request_id="qwen_c1_r001_req001",
        model_label="qwen",
        endpoint="http://example.com",
        file_id="case-file-id",
        concurrency=1,
        round_index=1,
        request_index=1,
        force=True,
        extract_model="qwen-extract",
        ocr_model="qwen",
        success=True,
        latency_ms=7000,
        http_status=200,
        json_valid=True,
        empty_result=False,
        extract={"reporter": "张三"},
    )

    calls = benchmark.enrich_llm_calls(benchmark.parse_llm_metrics_dir(str(tmp_path), "batch-2"), [result])
    chart_paths = benchmark.generate_metric_artifacts(tmp_path, calls)
    summary = benchmark.summarize_llm_metrics(calls)

    assert len(calls) == 2
    assert {item.stage for item in calls} == {"ocr", "extract"}
    assert summary
    assert any(path.endswith(".svg") for path in chart_paths)
    assert (tmp_path / "charts" / "ocr_page_latency_heatmap_c1.svg").exists()
    assert (tmp_path / "matrix" / "ocr_page_latency_matrix_c1.csv").exists()


def test_metrics_match_sanitized_request_id():
    benchmark = load_benchmark_module()
    raw_request_id = "ocr=Qwen2.5-VL-7B-Instruct,extract=default_c1_r001_req001"
    sanitized_request_id = "ocr_Qwen2.5-VL-7B-Instruct_extract_default_c1_r001_req001"
    result = benchmark.RequestResult(
        request_id=raw_request_id,
        model_label="ocr=Qwen2.5-VL-7B-Instruct,extract=default",
        endpoint="http://example.com",
        file_id="case-file-id",
        concurrency=1,
        round_index=1,
        request_index=1,
        force=True,
        extract_model="",
        ocr_model="Qwen2.5-VL-7B-Instruct",
        success=True,
        latency_ms=100,
        http_status=200,
        json_valid=True,
        empty_result=False,
        extract={"reporter": "张三"},
    )
    details = [benchmark.OcrDetail(sanitized_request_id, "/data/demo.pdf", 1, "", "", 10, 20, 30, 40, "/tmp/a.txt", "pdf_page")]
    timings = [benchmark.OcrFileTiming(sanitized_request_id, "/data/demo.pdf", 50, "qwen", False, "application/pdf", 1, 1)]
    calls = [benchmark.LlmCallMetric(sanitized_request_id, "ocr", "qwen", 1, 100, 200, 300, 400)]

    ocr_rows = benchmark.summarize_ocr_requests([result], details, timings)
    benchmark.enrich_llm_calls(calls, [result])

    assert ocr_rows[0]["ocr_page_count"] == 1
    assert ocr_rows[0]["ocr_file_count"] == 1
    assert calls[0].concurrency == 1
