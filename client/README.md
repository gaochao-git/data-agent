# 并发性能测试客户端

该目录提供了一个用于测试后端 `/api/complaint/init_analysis` 流式接口的命令行工具 `sse_benchmark.py`。

## 功能特点

- 支持指定并发数量（默认 5）同时上传相同 PDF 文件；
- 自动读取单条 SSE 流，统计以下阶段耗时：
  1. PDF 页面解析与文字提取；
  2. 文本摘要生成；
  3. 举报事项/诉求列表生成；
  4. 事项详细分析；
- 使用 [Rich](https://rich.readthedocs.io/) 输出阶段耗时及整体统计结果，便于观察性能波动。

## 快速开始

1. 安装依赖：

   ```bash
   pip install -r client/requirements.txt
   ```

2. 启动后端服务（确保可以访问 `http://127.0.0.1:5000`）。
3. 执行测试脚本（示例：5 个并发请求上传同一份 20 页 PDF）：

   ```bash
   python client/sse_benchmark.py /path/to/sample_20_pages.pdf \
     --base-url http://127.0.0.1:5000 \
     --requests 5 \
     --concurrency 5
   ```

脚本执行结束后，会输出每个请求的阶段耗时以及平均值、最小值和最大值等统计信息。

> 如果在 HTTPS 环境下需要跳过证书校验，可附加 `--insecure` 参数。
