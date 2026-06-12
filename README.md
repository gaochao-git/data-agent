# fxcjg

本项目为“投诉分析”相关的全栈系统，包含后端服务与前端界面，支持文档解析、OCR、事项抽取、法条分析与对话式交互等能力。

## 项目结构

- 后端：`server/`（FastAPI）
- 前端：`web/`（Vite + React）

## 快速入口

- 后端说明与启动方式：`server/README.md`
- 前端说明与启动方式：`web/README.md`

## 模块索引（重点功能）

- 投诉处理与分析：`server/apps/complaint_app/README.md`
- 文件字段抽取：`server/apps/word_fit_app/README.md`
- 对话助手：`server/apps/chat_assistant/README.md`
- 投诉分析工具链：`server/apps/complaint_tools/README.md`
- NL2SQL：`server/apps/chat_data_app/README.md`

## 文档参考

- 对话消息结构：`complaint_chat_messages.md`
- 业务与协议文档：`docs/`


# sync to modelscope

## add remote addr (first need only)
git remote add modelscope https://oauth2:ms-c8cba5f1-d7e4-4e0d-9406-4fed4ecc10f5@www.modelscope.cn/eason5870/fxcjg.git
git remote add modelscope
https://oauth2:ms-c8cba5f1-d7e4-4e0d-9406-4fed4ecc10f5@www.modelscope.cn/eason5870/fxcjg_v2.git
## first push
git push --set-upstream modelscope main:github-main
## normal push modelscope
git push modelscope main:github-main
git push origin release-20251211
git push modelscope release-20251211:github-release-20251211
git push modelscope_v2 main:github-main