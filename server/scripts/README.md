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
python scripts/crypto.py decrypt-env CHAT_DATA_API_KEY
```

通过 `CHAT_DATA_ENV` 显式指定环境，例如：

```bash
CHAT_DATA_ENV=uat python scripts/crypto.py encrypt '数据库密码'
```

也可使用根目录快捷入口：

```bash
python crypto.py encrypt '数据库密码'
```

## prune_checkpoints.py — LangGraph checkpoint 清理

独立运维脚本，只依赖 `pymysql`，不 import 任何项目模块。
语法按 **Python 3.6** 收敛（不用 `from __future__ import annotations`、不用 `datetime.fromisoformat`），
可直接跑在 CentOS 自带的 `python3` 上。

```bash
python scripts/prune_checkpoints.py dry-run     # 只统计，不写库
python scripts/prune_checkpoints.py execute     # 真正删除
```

`mode` 为**必填参数**，只接受 `dry-run` / `execute`，缺失或非法直接报错退出。

连接信息（库地址、端口、用户、密码、库名）是文件顶部的固定常量，按部署环境直接改文件。
脚本只操作 checkpointer 库，不碰业务库。

清理动作只有一个——**keep-latest 裁剪**：对「checkpoint 体积 > `MIN_MB` 且最后活动早于
`MIN_IDLE_DAYS` 天」的会话，每个命名空间只保留最新一份快照及其引用的 blob。
裁剪后会话仍可正常续聊，仅失去"从中间步骤回溯重跑"的能力（当前代码没有该入口）。

安全设计：

- `dry-run` 全程只读，不做任何写操作；确认输出后再用 `execute`。
- 每批删除独立提交（`BATCH_ROWS` 行/批，批间 `SLEEP_MS`），不使用大事务；锁冲突自动退避重试。
- 保留集按最新快照 `channel_versions` 的引用取，不按"每通道最大版本"取。
- 删除前重新复核该会话仍然闲置（发现到执行之间可能有人回来续聊）；
  活动时间解析不出来时一律跳过（fail-closed），不猜测。
- 删除后做对账式自检：保留行必须精确等于计划数，`messages` 快照必须仍可解析，
  输出 `自检 PASS/FAIL`。

建议放低峰定时执行（发现阶段要全库扫 `LENGTH(blob)`，千级会话约数分钟），例如每周一次：

```cron
0 4 * * 1 cd /path/to/server && python scripts/prune_checkpoints.py execute >> logs/prune.log 2>&1
```

注意：InnoDB 删除后不会把空间归还操作系统，页仅作 free 空间复用；确需缩表文件要低峰单独执行 `OPTIMIZE TABLE`。
