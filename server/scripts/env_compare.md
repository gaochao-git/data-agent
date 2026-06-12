# env_compare.py

`env_compare.py` 用于比较和同步两个 `.env` 文件。

## 基本命令

比较两个文件的 key 和 value：

```bash
python3 server/scripts/env_compare.py diff server/.env server/.env.psoss-sh20
```

查看某个 key 的值，默认不打印明文：

```bash
python3 server/scripts/env_compare.py get server/.env SOME_KEY
```

把 `server/.env` 中某个 key 的值同步到 `server/.env.psoss-sh20`：

```bash
python3 server/scripts/env_compare.py sync server/.env server/.env.psoss-sh20 SOME_KEY
```

## 输出规则

`diff` 会输出三类结果：

- 仅左侧文件存在的 key
- 仅右侧文件存在的 key
- 两边都存在但 value 不一致的 key

默认不打印真实 value，只显示长度和 hash，例如：

```text
<hidden len=16 sha256=xxxxxxxxxxxx>
```

如确实需要看明文，显式加：

```bash
python3 server/scripts/env_compare.py diff server/.env server/.env.psoss-sh20 --show-values
python3 server/scripts/env_compare.py get server/.env SOME_KEY --show-values
```

## 注释规则

`#` 开头的行不参与比较。

例如：

```env
# OPENAI_API_KEY=xxx
  # OCR_LLM_MODEL=xxx
ACTIVE_KEY=xxx
```

只会识别并比较 `ACTIVE_KEY`，不会比较注释掉的 `OPENAI_API_KEY` 和 `OCR_LLM_MODEL`。

## 同步规则

`sync` 只同步指定 key：

- 目标文件已有该 key：更新这一行的 value
- 目标文件没有该 key：追加到文件末尾
- 源文件没有该 key：直接报错，不修改目标文件

脚本会保留目标文件中已有的注释、空行和大部分原始顺序。
