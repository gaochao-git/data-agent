# expert-api Ansible 部署说明

本文档整理当前通过 Ansible 向 `store-be-grouped-hosts.ini` 中所有机器分发并启动 `expert-api` 的命令。

约定：
- 当前执行目录已包含：
  - `python312.tar.gz`
  - `expert-api-0410.tar.gz`
  - `file.tar.gz`
  - `store-be-grouped-hosts.ini`
- 远端机器可通过 Ansible 正常访问。
- 如远端 `sudo` 需要密码，请把下面命令中的 `-b` 改为 `-b -K`。

## 1. 连通性检查

```bash
ansible all -i store-be-grouped-hosts.ini -m ping
```

## 2. 创建远端目录

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "mkdir -p /usr/local /tools/apps /data/aigc/expert/file"
```

## 3. Python 运行时

上传 `python312.tar.gz`：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m copy -a "src=python312.tar.gz dest=/usr/local/python312.tar.gz mode=0644"
```

解压到 `/usr/local/`：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "tar -xzf /usr/local/python312.tar.gz -C /usr/local"
```

确认解压成功：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "ls -lah /usr/local | grep python"
```

## 4. expert-api 程序包

上传 `expert-api-0410.tar.gz`：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m copy -a "src=expert-api-0410.tar.gz dest=/tools/apps/expert-api-0410.tar.gz mode=0644"
```

如果压缩包顶层目录就是 `expert-api`，直接解压：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "cd /tools/apps && tar -xzf expert-api-0410.tar.gz"
```

## 5. file 数据包

上传 `file.tar.gz`：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m copy -a "src=file.tar.gz dest=/data/aigc/expert/file/file.tar.gz mode=0644"
```

解压到 `/data/aigc/expert/file/`：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "mkdir -p /data/aigc/expert/file && cd /data/aigc/expert/file && tar -xzf file.tar.gz"
```

确认解压成功：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "find /data/aigc/expert/file -maxdepth 2 | head -20"
```

## 6. 启动服务

后台执行 `start.sh`：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "cd /tools/apps/expert-api && chmod +x start.sh && ./start.sh &"
```

## 7. 启动校验

查看 8000 端口监听：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "ss -lntp | grep :8000 || netstat -lntp | grep :8000"
```

查看进程：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "ps -ef | grep 8000 | grep -v grep"
```

本机探测接口：

```bash
ansible all -i store-be-grouped-hosts.ini -b -m shell -a "curl -I http://127.0.0.1:8000 || true"
```

## 8. 推荐执行顺序

按下面顺序执行：

1. 连通性检查
2. 创建目录
3. 上传并解压 `python312.tar.gz`
4. 上传并解压 `expert-api-0410.tar.gz`
5. 上传并解压 `file.tar.gz`
6. 执行 `./start.sh &`
7. 检查 8000 端口和进程
