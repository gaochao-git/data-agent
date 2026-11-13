# Scripts

在 `server/` 目录执行配置加解密：

```bash
# 生成 16 字节 SM4 密钥，将输出填入对应 conf/.env-{env} 的 ENCRYPT_KEY
python scripts/crypto.py key

# 自动按本机 IP 匹配环境并加载对应 ENCRYPT_KEY 后加密
python scripts/crypto.py encrypt '数据库密码'

# 解密密文
python scripts/crypto.py decrypt 'base64(iv):base64(ciphertext)'

# 解密当前环境文件中的指定配置
python scripts/crypto.py decrypt-env OPENSANDBOX_API_KEY
```

通过 `CHAT_DATA_ENV` 显式指定环境，例如：

```bash
CHAT_DATA_ENV=uat python scripts/crypto.py encrypt '数据库密码'
```

也可使用根目录快捷入口：

```bash
python crypto.py encrypt '数据库密码'
```

## 共享 runtime 清理

```bash
# 默认清理 7 天前、没有被会话 ref 引用的共享 semantic，
# 以及过期的 skills.building-* / skills.old-* 临时目录
python scripts/cleanup_shared_runtime.py

# 只查看，不删除
python scripts/cleanup_shared_runtime.py --dry-run

# 自定义保留天数
python scripts/cleanup_shared_runtime.py --max-age-days 14
```

清理只针对 `CHAT_DATA_HOME/threads/{user_id}/runtime/`，不会删除会话 `user-data/`、技能管理源或插件。
建议在低峰期由单个维护任务执行，不要每个应用节点同时跑：

```cron
0 4 * * * cd /path/to/data-agent/server && python scripts/cleanup_shared_runtime.py --max-age-days 7
```

共享 runtime 清理需要保护 semantic ref；脚本已按 ref 保护，建议只由单个维护任务执行。

## 旧会话目录迁移到 workspace/.system

新版本把系统数据统一放到会话的 `workspace/.system/`。旧开发数据可按下面步骤迁移：

```bash
cd server

# 1. 先 dry-run，查看将移动多少文件
CHAT_DATA_HOME=/path/to/chat_data python scripts/migrate_workspace_system_layout.py

# 2. 确认后执行
CHAT_DATA_HOME=/path/to/chat_data python scripts/migrate_workspace_system_layout.py --apply
```

迁移映射：

```text
outputs/query_result/   -> workspace/.system/query_result/
outputs/.tool-offload/  -> workspace/.system/tool-offload/
```

脚本不处理旧 `workspace/semantic/`：应用会按当前数据源重新物化，旧文件不再读取。

脚本幂等，目标已存在时不覆盖；只处理会话 `user-data`，不碰用户技能、插件和运行缓存。

注意：历史消息里的旧 `result_path` 不会在数据库里自动改写，旧结果卡片可能需要重新查询或由前端按 `result_id` 重新定位。
