# CubeSandbox + E2B SDK 安装使用文档

本文记录 CubeSandbox 沙箱服务安装、模板创建、E2B SDK 验证以及后续接入智能体的配置方式。

## 1. 结论

CubeSandbox 可以作为智能体代码执行沙箱使用，推荐接入方式是：

```text
业务系统 / 智能体
  -> E2B Python SDK
  -> CubeAPI
  -> CubeSandbox 沙箱实例
```

E2B SDK 不是虚拟机，也不是 CubeSandbox 服务端组件。它是应用侧客户端 SDK，负责创建沙箱、执行代码、执行 Shell 命令、读写文件和销毁沙箱。

已验证能力：

- 创建沙箱
- Python 代码执行
- Shell 命令执行
- 文件写入和读取
- 沙箱销毁

## 2. 环境要求

CubeSandbox 需要支持 KVM 的 x86_64 Linux 环境。

建议环境：

- Ubuntu 22.04 x86_64
- `/dev/kvm` 可用
- Docker 可用
- glibc >= 2.34
- 内存、磁盘按并发沙箱规模预留

检查命令：

```bash
uname -m
ldd --version | head -n 1
ls -l /dev/kvm
egrep -c '(vmx|svm)' /proc/cpuinfo
docker version
```

## 3. CubeSandbox 安装

下载并解压 CubeSandbox one-click 包：

```bash
tar -zxvf cube-sandbox-one-click-46424e7.tar.gz
cd cube-sandbox-one-click-46424e7
```

按安装包说明执行安装脚本。安装完成后，默认服务目录为：

```text
/usr/local/services/cubetoolbox
```

常用入口：

```text
CubeAPI: http://<server-ip>:3000
WebUI:   http://<server-ip>:12088
```

健康检查：

```bash
curl http://127.0.0.1:3000/health
sudo bash /usr/local/services/cubetoolbox/scripts/one-click/quickcheck.sh
```

正常返回示例：

```json
{"status":"ok","sandboxes":0}
```

## 4. 创建代码执行模板

E2B SDK 创建沙箱时需要指定模板。本文使用官方代码执行镜像创建模板：

```bash
sudo /usr/local/services/cubetoolbox/CubeMaster/bin/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
  --template-id sandbox-code \
  --writable-layer-size 1G \
  --expose-port 49999 \
  --expose-port 49983 \
  --probe 49999 \
  --json
```

记录返回的 `job_id`，然后等待模板完成：

```bash
sudo /usr/local/services/cubetoolbox/CubeMaster/bin/cubemastercli tpl watch \
  --job-id <job_id> \
  --interval 2s \
  --json
```

成功后应看到：

```text
status: READY
template_id: sandbox-code
distribution: 1/1 ready, 0 failed
```

查看模板列表：

```bash
sudo /usr/local/services/cubetoolbox/CubeMaster/bin/cubemastercli tpl list --json
```

## 5. 安装 E2B SDK

建议先用独立 venv 验证：

```bash
sudo apt-get update
sudo apt-get install -y python3.10-venv

python3 -m venv /home/ubuntu/e2b-test-venv
source /home/ubuntu/e2b-test-venv/bin/activate

python -m pip install -U pip
pip install e2b e2b-code-interpreter
```

检查版本：

```bash
python - <<'PY'
import importlib.metadata as m
print("e2b", m.version("e2b"))
print("e2b-code-interpreter", m.version("e2b-code-interpreter"))
PY
```

已验证版本：

```text
e2b 2.25.0
e2b-code-interpreter 2.7.0
```

## 6. 配置证书信任

one-click 部署默认使用 `cube.app` 域名和 mkcert 自签证书。E2B SDK 访问沙箱时会连接：

```text
https://49983-<sandbox_id>.cube.app
```

Python 默认使用 certifi 证书链，可能不信任本机 mkcert CA，需要显式指定：

```bash
export SSL_CERT_FILE=$(ls /usr/local/share/ca-certificates/mkcert_development_CA_*.crt | head -n1)
```

生产环境建议改成正式域名和可信证书，避免依赖本地自签 CA。

## 7. E2B SDK 验证脚本

```bash
source /home/ubuntu/e2b-test-venv/bin/activate
export SSL_CERT_FILE=$(ls /usr/local/share/ca-certificates/mkcert_development_CA_*.crt | head -n1)

python - <<'PY'
from e2b_code_interpreter import Sandbox

api_key = "e2b_0000000000000000000000000000000000000000"
api_url = "http://127.0.0.1:3000"

sbx = None
try:
    sbx = Sandbox.create(template="sandbox-code", api_url=api_url, api_key=api_key, timeout=180)
    print("sandbox_id", sbx.sandbox_id)
    print("envd_api_url", sbx.envd_api_url)
    print("is_running", sbx.is_running())

    result = sbx.run_code("print('hello from e2b sdk')\nresult = 1 + 2\nresult")
    print("run_code_text", repr(result.text))

    cmd = sbx.commands.run("pwd && python3 --version && echo shell-ok", timeout=30)
    print("cmd_stdout", repr(cmd.stdout))
    print("cmd_stderr", repr(cmd.stderr))

    sbx.files.write("/tmp/e2b_test.txt", "file-ok")
    print("file_read", repr(sbx.files.read("/tmp/e2b_test.txt")))
finally:
    if sbx is not None:
        sbx.kill()
        print("killed", sbx.sandbox_id)
PY
```

成功输出示例：

```text
sandbox_id 8f5477ac08084bd08ec8a647c243dc58
envd_api_url https://49983-8f5477ac08084bd08ec8a647c243dc58.cube.app
is_running True
cmd_stdout '/home/user\nPython 3.12.12\nshell-ok\n'
cmd_stderr ''
file_read 'file-ok'
killed 8f5477ac08084bd08ec8a647c243dc58
```

## 8. 项目接入建议

建议不要在业务逻辑中直接调用 CubeAPI，而是在现有智能体沙箱执行层增加可选后端：

```text
WORKSPACE_EXECUTION_BACKEND=opensandbox | cubesandbox
```

CubeSandbox 相关配置建议：

```env
WORKSPACE_EXECUTION_BACKEND=cubesandbox
CUBESANDBOX_API_URL=http://<cube-server-ip>:3000
CUBESANDBOX_TEMPLATE=sandbox-code
CUBESANDBOX_API_KEY=e2b_0000000000000000000000000000000000000000
SSL_CERT_FILE=/usr/local/share/ca-certificates/mkcert_development_CA_xxx.crt
```

如果 CubeAPI 没开启鉴权，`CUBESANDBOX_API_KEY` 也需要满足 E2B SDK 的格式校验，即 `e2b_` 加 40 位十六进制字符。

建议抽象能力：

- `create_session`
- `run_command`
- `write_file`
- `read_file`
- `kill`
- `close`

这样上层智能体工具不需要感知 OpenSandbox 或 CubeSandbox 的差异。

## 9. 持久化能力验证

当前验证结论：

```text
同一个沙箱内文件持久：支持
Sandbox.connect 重新连接后文件还在：支持
pause/resume 后文件还在：支持
跨沙箱共享持久卷：当前不可用
宿主机目录直接挂载进沙箱：当前标准 E2B SDK 链路不可用
```

### 9.1 会话级持久化

同一个沙箱实例内，文件会随沙箱生命周期保留。已验证：

```text
写入 /tmp/session_probe.txt
Sandbox.connect 重新连接同一个 sandbox_id 后可以读到
pause/resume 后仍然可以读到
```

验证脚本：

```bash
source /home/ubuntu/e2b-test-venv/bin/activate
export SSL_CERT_FILE=$(ls /usr/local/share/ca-certificates/mkcert_development_CA_*.crt | head -n1)

python - <<'PY'
from e2b_code_interpreter import Sandbox

api_key = "e2b_0000000000000000000000000000000000000000"
api_url = "http://127.0.0.1:3000"

sbx = None
try:
    sbx = Sandbox.create(template="sandbox-code", api_url=api_url, api_key=api_key, timeout=120)
    sid = sbx.sandbox_id
    print("sid", sid)

    sbx.commands.run("echo session-persist-ok > /tmp/session_probe.txt")

    same = Sandbox.connect(sid, api_url=api_url, api_key=api_key, timeout=120)
    print("connect_read", same.commands.run("cat /tmp/session_probe.txt").stdout.strip())

    sbx.pause()
    resumed = Sandbox.connect(sid, api_url=api_url, api_key=api_key, timeout=120)
    print("resume_read", resumed.commands.run("cat /tmp/session_probe.txt").stdout.strip())
finally:
    if sbx is not None:
        sbx.kill()
PY
```

成功输出示例：

```text
connect_read session-persist-ok
resume_read session-persist-ok
```

### 9.2 E2B Volume 当前不可用

E2B SDK 提供 `Volume` 类：

```python
from e2b import Volume

Volume.create("fxcjg-test-volume", api_url=api_url, api_key=api_key)
Volume.list(api_url=api_url, api_key=api_key)
```

但当前 one-click CubeSandbox 环境返回：

```text
404
```

说明当前 CubeAPI 没有暴露 E2B Volume 管理接口，不能直接使用 E2B Volume 做跨沙箱持久卷。

### 9.3 volume_mounts 当前未生效

E2B SDK 的 `Sandbox.create` 支持 `volume_mounts` 参数：

```python
Sandbox.create(
    template="sandbox-code",
    api_url=api_url,
    api_key=api_key,
    volume_mounts={"/mnt/persist": "fxcjg-test-volume"},
)
```

但当前实测结果：

```text
沙箱内没有 /mnt/persist
跨两个沙箱无法共享文件
```

因此，虽然 SDK 参数存在，但当前 CubeSandbox 服务端没有通过该参数完成持久卷挂载。

### 9.4 与 OpenSandbox host volume 的差异

OpenSandbox 当前使用的宿主机目录挂载模式，本质是把服务端目录直接挂进沙箱，例如：

```text
/data/opensandbox-workspaces/<workspace_id> -> sandbox /mnt/...
```

当前 CubeSandbox + E2B SDK 标准链路不能直接等价替换这种 host volume 模式。

建议迁移策略：

```text
短期：应用侧管理工作区持久化
- 会话期间复用同一个 sandbox_id
- 执行前把需要的文件写入沙箱
- 执行后把产物读回应用存储
- 不依赖宿主机目录挂载

长期：让厂商提供明确支持
- E2B Volume API
- 或 CubeSandbox 官方 hostDir / host mount 文档和示例
```

## 10. 常见问题

### 10.1 template not found

现象：

```text
failed to get template param from store: template not found
```

原因：还没有创建可用模板，或 `template` 参数写错。

处理：

```bash
sudo /usr/local/services/cubetoolbox/CubeMaster/bin/cubemastercli tpl list --json
```

确认 `sandbox-code` 状态为 `READY`。

### 10.2 unknown service cubelet.services.images.v1.Images

现象：

```text
artifact distribution failed on all 1 nodes:
rpc error: code = Unimplemented desc = unknown service cubelet.services.images.v1.Images
```

本次验证中的原因：Cubelet 首次启动时 network-agent 尚未 ready，导致 `cubebox-service` 和 `images-service` 插件没有加载成功。

检查日志：

```bash
sudo grep -n "network-agent health check failed\|images-service\|cubebox-service" \
  /data/log/Cubelet/Cubelet-req.log | tail -n 120
```

处理方式：

```bash
curl http://127.0.0.1:19090/healthz

sudo pkill -f "/usr/local/services/cubetoolbox/Cubelet/bin/cubelet"
sudo bash -c 'nohup env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 SHELL=/bin/bash CUBE_SANDBOX_NODE_IP=<node-private-ip> /usr/local/services/cubetoolbox/Cubelet/bin/cubelet --config /usr/local/services/cubetoolbox/Cubelet/config/config.toml --dynamic-conf-path /usr/local/services/cubetoolbox/Cubelet/dynamicconf/conf.yaml >/var/log/cube-sandbox-one-click/cubelet-restart.log 2>&1 &'
```

重启后确认：

```bash
sudo /usr/local/services/cubetoolbox/scripts/one-click/quickcheck.sh
sudo grep -n "cubelet successfully booted\|images-service\|cubebox-service" \
  /data/log/Cubelet/Cubelet-req.log | tail -n 80
```

### 10.3 certificate verify failed

现象：

```text
SSL: CERTIFICATE_VERIFY_FAILED
```

原因：E2B SDK 访问 `https://49983-<sandbox_id>.cube.app`，但 Python 不信任 mkcert 自签 CA。

处理：

```bash
export SSL_CERT_FILE=$(ls /usr/local/share/ca-certificates/mkcert_development_CA_*.crt | head -n1)
```

生产建议使用正式证书。

### 10.4 CubeAPI 3000 页面打不开

`3000` 是 API，不是 Web 页面。

```text
CubeAPI: http://<server-ip>:3000/health
WebUI:   http://<server-ip>:12088/
```

## 11. 本次验证环境记录

```text
OS: Ubuntu 22.04.5
glibc: 2.35
Docker: 29.1.3
CubeSandbox: v0.2.2 one-click package 46424e7
CubeAPI: http://152.136.104.189:3000
WebUI: http://152.136.104.189:12088
Template: sandbox-code
```

注意：服务器 IP 是本次临时验证环境，正式接入时应替换为内网稳定地址或正式域名。
