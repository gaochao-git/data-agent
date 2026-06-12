当前返回结构

  顶层是：

  {
    "base_id": "...",
    "items": [ ... ],
    "total": 1,
    "page": 1,
    "page_size": 50,
    "user_id": "...",
    "file_ids": ["..."],
    "scope_context": {...},
    "message": "查询成功",
    "exists": true
  }

  每个 item 是：

  {
    "base_id": "...",
    "complaint_analysis_v2_version": 3,
    "created_at": "2026-06-07 15:30:12",
    "summary": {
      "content": "..."
    },
    "analysis": {
      "content": "..."
    },
    "reference": {
      "content": "..."
    },
    "final": {
      "content": "..."
    },
    "versions": {
      "text_version": 1,
      "summary_v2_version": 2,
      "analysis_v2_version": 3,
      "complaint_analysis_v2_version": 4
    }
  }