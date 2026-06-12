
## 基于本地法律进行举报分析

```bash
 python scripts/run_opencode_runtime.py \
    --use-api \
    --user-id 10001 \
    --user-name fys \
    -p "基于举报件生成摘要，并使用本地laws对举报事项分析" \
    --show-plan \
    --show-steps \
    --attachment /Users/fengyingsheng/Downloads/mock_举报信_5图片_交易流水.pdf
```

## 举报事项分析

```bash
python scripts/run_opencode_runtime.py \
    --use-api \
    --user-id 10001 \
    --user-name fys \
    -p "基于举报件生成摘要和事项分析，并使用专家经验分析举报事项" \
    --show-plan \
    --show-steps \
    --attachment /Users/fengyingsheng/Downloads/mock_举报信_5图片_交易流水.pdf
```

## 查看tool信息

```bash
python scripts/debug_tools_registry.py
```

## 直接执行skill的命令

```bash
python scripts/run_skill_runtime.py \
    --skill complaint-law-analysis \
    --prompt "基于举报件生成摘要，并使用本地laws对举报事项分析" \
    --source-text-file ./texts/v1/final.txt \
    --show-steps
```

## complaint-analysis 并发压测

```bash
python3 scripts/complaint_analysis_benchmark.py   --base-url http://127.0.0.1:5001   --user-id api_user   --file-id YOUR_FILE_ID_1   --file-id YOUR_FILE_ID_2   --file-id YOUR_FILE_ID_3   --concurrency 3   --rounds 1
```

如果 `file_id` 很多，也可以放到文本文件里，一行一个：

```bash
python3 scripts/complaint_analysis_benchmark.py   --base-url http://127.0.0.1:5001   --user-id api_user   --file-id-file ./file_ids.txt   --concurrency 10   --rounds 1
```

输出目录下会生成：
- `summary.json`：整轮聚合结果
- `summary.csv`：每个请求一行的汇总
- `round_xx/.../events.jsonl`：每个请求的原始 SSE 明细
- `round_xx/.../summary.json`：每个请求的阶段耗时与状态
- `phase_summary.csv`：按阶段聚合的耗时统计（秒，count / avg / p50 / p95 / max）
