#!/bin/bash

# Web启动脚本
# 自动检测端口占用并启动开发服务器

PORT=3000
PROJECT_DIR="/Users/fengyingsheng/workspaces/personal/fxcjg/fxcjg/web"

echo "🚀 启动Web开发服务器..."
echo "📁 项目目录: $PROJECT_DIR"
echo "🔌 目标端口: $PORT"

# 检查端口是否被占用
check_port() {
    if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null ; then
        echo "⚠️  端口 $PORT 已被占用"
        
        # 获取占用端口的进程信息
        PID=$(lsof -ti :$PORT)
        PROCESS_NAME=$(ps -p $PID -o comm= 2>/dev/null)
        
        if [ ! -z "$PID" ]; then
            echo "📋 占用进程: $PROCESS_NAME (PID: $PID)"
            
            # 询问是否终止现有进程
            read -p "❓ 是否终止现有进程并重新启动? (y/n): " -n 1 -r
            echo
            
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                echo "🔄 正在终止进程 $PID..."
                kill -9 $PID
                sleep 2
                
                # 再次检查端口是否释放
                if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null ; then
                    echo "❌ 端口释放失败，请手动处理"
                    exit 1
                else
                    echo "✅ 端口已释放"
                fi
            else
                echo "❌ 用户取消操作"
                exit 1
            fi
        fi
    else
        echo "✅ 端口 $PORT 可用"
    fi
}

# 切换到项目目录
cd "$PROJECT_DIR" || {
    echo "❌ 无法切换到项目目录: $PROJECT_DIR"
    exit 1
}

# 检查端口
check_port

# 检查是否安装了依赖
if [ ! -d "node_modules" ]; then
    echo "📦 检测到缺少依赖，正在安装..."
    pnpm install
fi

echo "🎯 启动开发服务器..."
echo "🌐 访问地址: http://localhost:$PORT"
echo "⏹️  按 Ctrl+C 停止服务器"
echo ""

# 启动开发服务器
pnpm run dev:client