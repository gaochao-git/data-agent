from pathlib import Path

from dotenv import load_dotenv

# 独立进程：显式加载 server/.env（与主应用同一份配置），再初始化配置读取
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from mcp_servers.db_query_server.server import main

main()
