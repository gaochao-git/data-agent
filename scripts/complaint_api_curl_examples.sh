#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:5001}"
FILE_ID="${1:-/path/to/your/file.pdf}"
PDF_FILE="${2:-}"
USER_ID="${USER_ID:-api_user}"

echo "[1] 举报事项分析 SSE（file_id 模式）"
curl -N -X POST "${BASE_URL}/api/complaint/complaint-analysis" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"${USER_ID}\",\"file_id\":\"${FILE_ID}\",\"force\":false}"

if [[ -n "${PDF_FILE}" ]]; then
  echo "[1.1] 举报事项分析 SSE（上传 PDF 模式）"
  curl -N -X POST "${BASE_URL}/api/complaint/complaint-analysis" \
    -F "user_id=${USER_ID}" \
    -F "use_uploaded_pdf=true" \
    -F "force=false" \
    -F "file=@${PDF_FILE};type=application/pdf"
else
  echo "[1.1] 未传入第2个参数 PDF_FILE，跳过上传 PDF 示例"
fi

echo "[2] 辅助文书撰写 SSE（file_id 模式）"
curl -N -X POST "${BASE_URL}/api/complaint/reply-draft" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"${USER_ID}\",\"file_id\":\"${FILE_ID}\",\"force\":false}"

echo "[3] 保存草稿"
curl -X POST "${BASE_URL}/api/complaint/reply-draft/save" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"${USER_ID}\",\"file_id\":\"${FILE_ID}\",\"title\":\"测试答复意见书\",\"content\":\"这是草稿内容\",\"source_versions\":{}}"

echo "[4] 下载 Word"
curl -L "${BASE_URL}/api/complaint/reply-draft/download?file_id=${FILE_ID}&user_id=${USER_ID}&version=latest" -o reply_draft.docx

echo "done"
