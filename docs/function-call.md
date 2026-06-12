## 步骤1:定义工具函数

# 添加导入random模块
import random
from datetime import datetime

# 模拟天气查询工具。返回结果示例：“北京今天是雨天。”
def get_current_weather(arguments):
    # 定义备选的天气条件列表
    weather_conditions = ["晴天", "多云", "雨天"]
    # 随机选择一个天气条件
    random_weather = random.choice(weather_conditions)
    # 从 JSON 中提取位置信息
    location = arguments["location"]
    # 返回格式化的天气信息
    return f"{location}今天是{random_weather}。"

# 查询当前时间的工具。返回结果示例：“当前时间：2024-04-15 17:15:18。“
def get_current_time():
    # 获取当前日期和时间
    current_datetime = datetime.now()
    # 格式化当前日期和时间
    formatted_time = current_datetime.strftime('%Y-%m-%d %H:%M:%S')
    # 返回格式化后的当前时间
    return f"当前时间：{formatted_time}。"

# 测试工具函数并输出结果，运行后续步骤时可以去掉以下四句测试代码
print("测试工具输出：")
print(get_current_weather({"location": "上海"}))
print(get_current_time())
print("\n")


## 步骤2:创建 tools 数组

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "当你想知道现在的时间时非常有用。",
            "parameters": {}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "当你想查询指定城市的天气时非常有用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "城市或县区，比如北京市、杭州市、余杭区等。",
                    }
                },
                "required": ["location"]
            }
        }
    }
]
tool_name = [tool["function"]["name"] for tool in tools]
print(f"创建了{len(tools)}个工具，为：{tool_name}\n")

# 步骤3:创建messages数组
# 请将以下代码粘贴到步骤2 代码后
# 文本生成模型的 User Message示例
messages = [
    {
        "role": "system",
        "content": """你是一个很有帮助的助手。如果用户提问关于天气的问题，请调用 ‘get_current_weather’ 函数;
     如果用户提问关于时间的问题，请调用‘get_current_time’函数。
     请以友好的语气回答问题。""",
    },
    {
        "role": "user",
        "content": "上海天气"
    }
]

# Qwen3-VL模型的 User Message示例
# messages=[
#  {
#         "role": "system",
#         "content": """你是一个很有帮助的助手。如果用户提问关于天气的问题，请调用 ‘get_current_weather’ 函数;
#      如果用户提问关于时间的问题，请调用‘get_current_time’函数。
#      请以友好的语气回答问题。""",
#     },
#     {"role": "user",
#      "content": [{"type": "image_url","image_url": {"url": "https://img.alicdn.com/imgextra/i2/O1CN01FbTJon1ErXVGMRdsN_!!6000000000405-0-tps-1024-683.jpg"}},
#                  {"type": "text", "text": "根据图像上的地点，查询该地点当前天气"}]},
# ]

print("messages 数组创建完成\n") 


# 步骤4:发起 function calling
# 请将以下代码粘贴到步骤3 代码后

from openai import OpenAI
import os

client = OpenAI(
    # 若没有配置环境变量，请用阿里云百炼API Key将下行替换为：api_key="sk-xxx",
    # 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    # 以下是北京地域base_url，如果使用新加坡地域的模型，需要将base_url替换为：https://dashscope-intl.aliyuncs.com/compatible-mode/v1
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

def function_calling():
    completion = client.chat.completions.create(
        model="qwen-plus",  # 此处以qwen-plus为例，可按需更换模型名称。模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
        messages=messages,
        tools=tools
    )
    print("返回对象：")
    print(completion.choices[0].message.model_dump_json())
    print("\n")
    return completion

print("正在发起function calling...")
completion = function_calling()

# 步骤5:运行工具函数
# 请将以下代码粘贴到步骤4 代码后
import json

print("正在执行工具函数...")
# 从返回的结果中获取函数名称和入参
function_name = completion.choices[0].message.tool_calls[0].function.name
arguments_string = completion.choices[0].message.tool_calls[0].function.arguments

# 使用json模块解析参数字符串
arguments = json.loads(arguments_string)
# 创建一个函数映射表
function_mapper = {
    "get_current_weather": get_current_weather,
    "get_current_time": get_current_time
}
# 获取函数实体
function = function_mapper[function_name]
# 如果入参为空，则直接调用函数
if arguments == {}:
    function_output = function()
# 否则，传入参数后调用函数
else:
    function_output = function(arguments)
# 打印工具的输出
print(f"工具函数输出：{function_output}\n")

# 步骤6:向大模型提交工具输出
# 请将以下代码粘贴到步骤5 代码后

messages.append(completion.choices[0].message)
print("已添加assistant message")
messages.append({"role": "tool", "content": function_output, "tool_call_id": completion.choices[0].message.tool_calls[0].id})
print("已添加tool message\n")

# 步骤7:大模型总结工具输出
# 请将以下代码粘贴到步骤6 代码后
print("正在总结工具输出...")
completion = function_calling()



from openai import OpenAI
import os

client = OpenAI(
    # 若没有配置环境变量，请用阿里云百炼API Key将下行替换为：api_key="sk-xxx",
    # 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    # 以下是北京地域base_url，如果使用新加坡地域的模型，需要将base_url替换为：https://dashscope-intl.aliyuncs.com/compatible-mode/v1
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "当你想查询指定城市的天气时非常有用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "城市或县区，比如北京市、杭州市、余杭区等。",
                    }
                },
                "required": ["location"],
            },
        },
    },
]

stream = client.chat.completions.create(
    model="qwen-plus",
    messages=[{"role": "user", "content": "杭州天气?"}],
    tools=tools,
    stream=True
)

for chunk in stream:
    delta = chunk.choices[0].delta
    print(delta.tool_calls)