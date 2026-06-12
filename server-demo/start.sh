#!/bin/bash

# Flask OCR API 启动脚本

echo "🚀 启动 Flask OCR API 服务..."
echo "📋 检查环境变量配置..."

# 检查 .env 文件是否存在
if [ ! -f ".env" ]; then
    echo "❌ 错误: .env 文件不存在！"
    echo "请确保 .env 文件包含以下配置:"
    echo "DIFY_OCR_API_KEY=your_ocr_key"
    echo "DIFY_INFER_API_KEY=your_inference_key"
    exit 1
fi

# 激活 Conda 环境
echo "🔧 激活 Conda 环境 py39-jiangchi..."
if ! command -v conda >/dev/null 2>&1; then
    echo "❌ 错误: 未找到 conda 命令，请安装并初始化 Conda。"
    echo "Mac 常见位置: ~/miniconda3 或 ~/anaconda3"
    exit 1
fi
CONDA_BASE="$(conda info --base)"
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate py39-jiangchi || { echo "❌ 错误: 无法激活环境 py39-jiangchi"; exit 1; }

# 安装依赖（在当前 Conda 环境中）
echo "📦 检查并安装依赖..."
python -m pip install -r requirements.txt

# 创建必要的目录
echo "📁 创建上传目录..."
mkdir -p uploads/images uploads/texts

# 启动服务
echo "✅ 启动 Flask 服务 (开发模式 - 支持热重载)..."
echo "🌐 服务地址: http://127.0.0.1:5001"
echo "📖 API文档: 请查看 README.md"
echo "🔄 热重载: 代码修改后自动重启"
echo "⏹️  停止服务: 按 Ctrl+C"

# 设置Flask开发环境变量
export FLASK_ENV=development
export FLASK_DEBUG=1

python main.py