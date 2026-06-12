# UAT 同步生产时排除 venv 关键文件

## 目的

在 `fxcjg/` 目录下打包 `server/` 时，不携带 UAT 的 `.venv/bin` 和 `.venv/pyvenv.cfg`，避免 ansible 全量同步后影响生产环境启动。

适用前提：

- UAT 和生产的 Python 主版本一致
- UAT 和生产的系统环境基本一致
- 生产环境保留自己的 `.venv/bin` 和 `.venv/pyvenv.cfg`

## 打包命令

在 `fxcjg/` 目录下执行：

```bash
tar -zcvf server0402.tar.gz \
  --exclude='./server/.venv/bin' \
  --exclude='./server/.venv/pyvenv.cfg' \
  ./server/
```

如果想更稳一点，可以再排除：

```bash
tar -zcvf server0402.tar.gz \
  --exclude='./server/.venv/bin' \
  --exclude='./server/.venv/pyvenv.cfg' \
  --exclude='./server/.venv/include' \
  --exclude='./server/.venv/share' \
  ./server/
```

## 验证压缩包内容

打包完成后，检查 `.venv` 里是否还包含不该带的内容：

```bash
tar -ztvf server0402.tar.gz | rg '.venv'
```

预期：

- 不应出现 `./server/.venv/bin/...`
- 不应出现 `./server/.venv/pyvenv.cfg`

## 说明

这种做法的本质是：

- 同步 UAT 的代码和大部分依赖内容
- 保留生产自己的 venv 启动入口

这样 ansible 推送后，通常不会破坏生产环境原有的 Python 启动路径。
