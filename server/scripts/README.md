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


## 派生缓存清理

```bash
# 默认清理 7 天前未被会话 ref 引用的 runtime-skills / semantic 派生缓存
python scripts/cleanup_chat_data_cache.py

# 只查看，不删除
python scripts/cleanup_chat_data_cache.py --dry-run

# 自定义保留天数
python scripts/cleanup_chat_data_cache.py --max-age-days 14
```

清理只针对 `CHAT_DATA_CACHE_HOME` 的新布局目录（`runtime-skills/{user}/`、`semantic/{user}/{datasource}/`），不会删除共享 `CHAT_DATA_HOME` 或数据库记录；升级前的历史目录不在运行期 GC 范围内，需按只读扫描结果由运维单独处理。
建议低峰期定时执行，例如每天 04:00：

```cron
0 4 * * * cd /path/to/data-agent/server && python scripts/cleanup_chat_data_cache.py --max-age-days 7
```
