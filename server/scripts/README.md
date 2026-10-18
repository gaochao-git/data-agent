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

