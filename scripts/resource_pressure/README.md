# Resource Pressure Scripts

这 3 个脚本用于在测试环境中将机器的 CPU、内存、磁盘占用维持在目标区间内。

默认目标区间为 `22%-28%`，均支持通过参数覆盖。

## 脚本列表

### Shell 版本

- `cpu_pressure.sh`：按机器总 CPU 核数动态调整 worker，把总 CPU 使用率维持在区间内。
- `memory_pressure.sh`：通过 `/dev/shm` 持续增减压力文件，把总内存使用率维持在区间内。
- `disk_pressure.sh`：向目标目录单次补写文件；达到目标区间后退出。

### Python 版本

- `cpu_pressure.py`
- `memory_pressure.py`
- `disk_pressure.py`

## 使用示例

```bash
bash scripts/resource_pressure/cpu_pressure.sh
bash scripts/resource_pressure/memory_pressure.sh
bash scripts/resource_pressure/disk_pressure.sh
```

当前脚本行为如下：

- `cpu_pressure.sh`：持续采样并动态增减 worker
- `memory_pressure.sh`：持续采样并动态增减内存压力文件
- `disk_pressure.sh`：单次补写；达到目标区间后自动退出

其中：

- `cpu_pressure.sh`、`memory_pressure.sh`：常驻守护，直到收到 `SIGTERM/SIGINT`
- `disk_pressure.sh`：跑一次即可，达到目标区间后自动退出

## 推荐启动方式

### 1. 手动后台启动

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg
nohup /usr/bin/bash scripts/resource_pressure/cpu_pressure.sh >> /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/logs/cpu_pressure.log 2>&1 &
nohup /usr/bin/bash scripts/resource_pressure/memory_pressure.sh >> /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/logs/memory_pressure.log 2>&1 &
/usr/bin/bash scripts/resource_pressure/disk_pressure.sh >> /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/logs/disk_pressure.log 2>&1
```

### 2. crontab 保活

如果需要在脚本异常退出后自动补拉起，可执行：

如果需要每 4 小时巡检一次，并在脚本未运行时自动拉起，可执行：

```bash
crontab -e
```

加入以下配置：

```cron
0 */4 * * * pgrep -f "scripts/resource_pressure/cpu_pressure.sh" >/dev/null || (cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg && /usr/bin/bash scripts/resource_pressure/cpu_pressure.sh >> /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/logs/cpu_pressure.log 2>&1)
0 */4 * * * pgrep -f "scripts/resource_pressure/memory_pressure.sh" >/dev/null || (cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg && /usr/bin/bash scripts/resource_pressure/memory_pressure.sh >> /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/logs/memory_pressure.log 2>&1)
```

含义：

- `0 */4 * * *`：每 4 小时执行一次保活检查，执行时间为 `00:00 / 04:00 / 08:00 / 12:00 / 16:00 / 20:00`
- `pgrep -f ... || (...)`：如果脚本已经在运行，就跳过；如果脚本挂了，才重新启动
- `>> ... 2>&1`：将标准输出和错误输出都追加到日志文件

磁盘脚本不建议加入这组保活 `crontab`，因为它是单次写盘脚本，默认写入目录为 `/data/tmp-disk-pressure`。

查看当前 crontab：

```bash
crontab -l
```

## 跳板机批量执行

### 推荐：Ansible 版

如果跳板机已安装 Ansible，并且目标机器支持免密 SSH，优先使用：

- [ansible_manage.sh](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/scripts/resource_pressure/ansible_manage.sh)
- [ansible_manage.yml](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/scripts/resource_pressure/ansible_manage.yml)
- [hosts.ini.example](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/scripts/resource_pressure/hosts.ini.example)

先准备 inventory，例如：

```ini
[resource_pressure]
10.0.0.11
10.0.0.12
10.0.0.13

[bj12]
10.0.0.11
10.0.0.12

[other_idc]
10.0.0.13
```

执行方式：

```bash
bash scripts/resource_pressure/ansible_manage.sh start scripts/resource_pressure/hosts.ini
bash scripts/resource_pressure/ansible_manage.sh status scripts/resource_pressure/hosts.ini
bash scripts/resource_pressure/ansible_manage.sh stop scripts/resource_pressure/hosts.ini
bash scripts/resource_pressure/ansible_manage.sh cleanup-disk scripts/resource_pressure/hosts.ini
```

调高区间时也一样：

```bash
MIN_PERCENT=30 MAX_PERCENT=35 CPU_GAIN=2.2 bash scripts/resource_pressure/ansible_manage.sh start scripts/resource_pressure/hosts.ini
MIN_PERCENT=30 MAX_PERCENT=35 bash scripts/resource_pressure/ansible_manage.sh status scripts/resource_pressure/hosts.ini
```

如果你只想对某一个分组执行命令，例如 `bj12` 组，把 `/tools/apps/server/.env.psoss-bj12` 改名成 `.env`，直接执行：

```bash
ansible bj12 -i scripts/resource_pressure/hosts.ini -m shell -a "if [ -f /tools/apps/server/.env.psoss-bj12 ]; then mv /tools/apps/server/.env.psoss-bj12 /tools/apps/server/.env; fi"
```

如果你希望覆盖已有 `.env`，先备份再替换：

```bash
ansible bj12 -i scripts/resource_pressure/hosts.ini -m shell -a "if [ -f /tools/apps/server/.env.psoss-bj12 ]; then [ -f /tools/apps/server/.env ] && cp /tools/apps/server/.env /tools/apps/server/.env.bak.$(date +%Y%m%d%H%M%S); mv /tools/apps/server/.env.psoss-bj12 /tools/apps/server/.env; fi"
```

如果磁盘指标还差一点点，可直接把目标区间抬高一点，或者减小写盘粒度：

```bash
MIN_PERCENT=22.5 MAX_PERCENT=28 CHUNK_MB=128 bash scripts/resource_pressure/ansible_manage.sh start scripts/resource_pressure/hosts.ini
MIN_PERCENT=23 MAX_PERCENT=28 CHUNK_MB=64 bash scripts/resource_pressure/ansible_manage.sh start scripts/resource_pressure/hosts.ini
```

### 兼容：remote_manage.sh

如果你有一台跳板机，并且已经能免密登录多台目标机器，可以直接使用：

- [remote_manage.sh](/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/scripts/resource_pressure/remote_manage.sh)

先准备主机清单 `hosts.txt`，每行一个主机，支持注释：

```text
10.0.0.11
10.0.0.12
# 这是一条注释
10.0.0.13
```

默认会读取：

```text
scripts/resource_pressure/hosts.txt
```

在跳板机上执行：

```bash
cd /Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg
bash scripts/resource_pressure/remote_manage.sh start
```

常用命令：

```bash
bash scripts/resource_pressure/remote_manage.sh start
bash scripts/resource_pressure/remote_manage.sh status
bash scripts/resource_pressure/remote_manage.sh stop
bash scripts/resource_pressure/remote_manage.sh cleanup-disk
```

如果你想把目标区间调高到 `30%-35%`，可以直接在命令前传环境变量：

```bash
MIN_PERCENT=30 MAX_PERCENT=35 CPU_GAIN=2.2 bash scripts/resource_pressure/remote_manage.sh start
MIN_PERCENT=30 MAX_PERCENT=35 bash scripts/resource_pressure/remote_manage.sh status
```

单机直接启动时也一样：

```bash
ssh 目标机器 "mkdir -p /tmp/resource_pressure_logs && setsid -f bash -lc \"nohup env MIN_PERCENT=30 MAX_PERCENT=35 CPU_GAIN=2.2 bash /tmp/resource_pressure/cpu_pressure.sh >> /tmp/resource_pressure_logs/cpu_pressure.log 2>&1 </dev/null\""
ssh 目标机器 "mkdir -p /tmp/resource_pressure_logs && setsid -f bash -lc \"nohup env MIN_PERCENT=30 MAX_PERCENT=35 bash /tmp/resource_pressure/memory_pressure.sh >> /tmp/resource_pressure_logs/memory_pressure.log 2>&1 </dev/null\""
```

可覆盖的环境变量：

- `HOSTS_FILE`：主机清单路径
- `REMOTE_BASE`：远端脚本目录，默认 `/tmp/resource_pressure`
- `REMOTE_LOG_DIR`：远端日志目录，默认 `/tmp/resource_pressure_logs`
- `REMOTE_DISK_TARGET`：远端磁盘压力目录，默认 `/data/tmp-disk-pressure`
- `MIN_PERCENT`：目标区间下限，默认 `22`
- `MAX_PERCENT`：目标区间上限，默认 `28`
- `CHUNK_MB`：磁盘脚本基础写盘块大小，默认 `256`

CPU 脚本额外支持：

- `CPU_GAIN`：CPU 反馈增益，默认 `1.8`。如果大核机器上 CPU 压力长期打不上目标区间，可适当调大，例如 `2.2`
- `MAX_WORKERS`：CPU 压力脚本允许拉起的最大 worker 数，默认等于机器 CPU 核数

## 单机排障命令

如果跳板机批量执行后显示 `not running`，建议先挑 1 台目标机器做最小排障。以下命令都在**跳板机**上执行：

```bash
ssh 目标机器 'ls -l /tmp/resource_pressure'
ssh 目标机器 "mkdir -p /tmp/resource_pressure_logs && setsid -f bash -lc \"nohup bash /tmp/resource_pressure/cpu_pressure.sh >> /tmp/resource_pressure_logs/cpu_pressure.log 2>&1 </dev/null\""
ssh 目标机器 "mkdir -p /tmp/resource_pressure_logs && setsid -f bash -lc \"nohup bash /tmp/resource_pressure/memory_pressure.sh >> /tmp/resource_pressure_logs/memory_pressure.log 2>&1 </dev/null\""
ssh 目标机器 "pgrep -af '[c]pu_pressure.sh'; pgrep -af '[m]emory_pressure.sh'"
ssh 目标机器 "tail -n 50 /tmp/resource_pressure_logs/cpu_pressure.log /tmp/resource_pressure_logs/memory_pressure.log"
```

用途说明：

- 第 1 条：确认脚本是否已经同步到远端 `/tmp/resource_pressure`
- 第 2 条：用更稳的 `setsid -f` 手工启动 CPU 压力脚本
- 第 3 条：用更稳的 `setsid -f` 手工启动内存压力脚本
- 第 4 条：单独检查 CPU/内存脚本进程是否存在
- 第 5 条：查看 CPU/内存脚本的远端日志，判断是否启动即退出

## 注意事项

- 仅建议在测试/UAT 环境使用，不要直接在生产环境执行。
- `disk_pressure.sh` / `disk_pressure.py` 都会真实写盘，占用磁盘空间；停止后不会自动清理，需手动执行 `--cleanup`。
- `cpu_pressure.sh`、`memory_pressure.sh` 支持常驻守护；`disk_pressure.sh` 为单次写盘脚本。
- 磁盘脚本不会主动删文件；如果当前磁盘使用率已高于目标区间，会直接退出，不会自动回收。
- `memory_pressure.sh` 依赖 `/dev/shm`；若机器未挂载 tmpfs 或容量过小，请继续使用 Python 版本。
