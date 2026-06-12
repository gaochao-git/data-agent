# upload_page.py

`upload_page.py` 是一个独立的 FastAPI 文件浏览和上传页面，运行在 `10081` 端口。它不属于 `server-demo`，后续维护只看 `server/apps/upload_page.py`。

## 访问路径

线上通过 APISIX 代理到本服务，APISIX 会把这些路径转发到当前 Python 页面：

- `/`
- `/ai/*`
- `/ai/upload*`
- `/ai/download/*`
- `/ai/view/*`

因此服务端必须同时支持根路径和 `/ai` 前缀路径。页面里生成的浏览器链接也应保持 `/ai/...` 形式，例如：

- `/ai/upload`
- `/ai/?path=xxx`
- `/ai/view/xxx.md`
- `/ai/download/xxx.md`
- `/ai/submit`

如果只生成 `/view/...` 或相对路径，经过 APISIX 代理时容易打到错误路径并返回 `{detail: "Not Found"}`。

## 文件根目录

这个页面不是固定浏览 `server/apps`。它应该浏览启动时指定的目录。

启动或重启时用第二个参数指定文件根目录：

```bash
python3 server/apps/upload_page.py restart /path/to/files
```

服务会把该目录作为 `STORAGE_DIR`，之后列表、上传、下载和预览都基于这个目录。当前根目录会写入：

```text
server/logs/upload_page.root
```

如果 `restart` 不带目录参数，会优先复用 `server/logs/upload_page.root` 中记录的目录；没有记录时才使用 `UPLOAD_PAGE_STORAGE_DIR` 或默认目录。

## 后台管理命令

```bash
python3 server/apps/upload_page.py start /path/to/files
python3 server/apps/upload_page.py stop
python3 server/apps/upload_page.py restart /path/to/files
python3 server/apps/upload_page.py status
```

后台运行信息：

```text
server/logs/upload_page.pid
server/logs/upload_page.log
server/logs/upload_page.root
```

`status` 需要显示当前 pid、root 和访问地址，方便确认是否正在浏览正确目录。

## 预览能力

`/ai/view/{filepath}` 支持这些类型：

- `.pdf`：浏览器内联预览
- `.json`：结构化 JSON 预览
- `.jsonl`：按行解析并结构化预览
- `.md`：按 Markdown 格式渲染预览，并保留原文切换
- `.ttl`：结构化 TTL 预览，并保留原文切换
- `.sparql`：结构化 SPARQL 预览，并保留原文切换
- 其他文本扩展名：纯文本预览

Markdown 预览目前是轻量实现，覆盖标题、段落、加粗、斜体、行内代码、代码块、引用、无序列表、链接和表格。渲染前必须做 HTML 转义，避免把文件内容当成可信 HTML。

## 调试检查

修改 URI 或启动逻辑后，至少验证：

```bash
python3 -m py_compile server/apps/upload_page.py
python3 server/apps/upload_page.py restart /path/to/files
curl -i http://localhost:10081/ai/upload
curl -i http://localhost:10081/ai/view/some-file.md
curl -i http://localhost:10081/ai/download/some-file.md
```

如果 APISIX 返回 `{detail: "Not Found"}`，优先检查 Python 服务是否注册了对应的 `/ai/...` 路由，以及页面生成的链接是否仍然带 `/ai` 前缀。
