

# chat的接口协议定义

URL： /api/chat/completion
method: POST
content-type: application/json
## rquest params：

"agent_id": "complaint_agent" # 举报件办理
"agent_id": "expert_agent" # 法律检索

```json
{
    "client_meta": {
        "conversation_id": "33920597455202306",
        "agent_id": "complaint_agent"
        "last_message_index": 5
    },
    "messages": [
        {
            "content_block": [
                {
                    "block_type": 10000,
                    "content": {
                        "reference_block": {
                            "type": 6,
                            "text": {
                                "text": "The O’Reilly logo is a registered trademark of O’Reilly Media, Inc. HandsOn Large Language Models, the cover image, and related trade dress are trademarks of O’Reilly Media, Inc.",
                                "message_id": "",
                                "sub_index": 0
                            }
                        }
                    },
                    "block_id": "302ac1b0-4308-4a47-9655-92aa2876b04d",
                    "meta_info": []
                },
                {
                    "block_type": 10001,
                    "content": {
                        "text_block": {
                            "text": "翻译",
                            "icon_url": "",
                            "icon_url_dark": "",
                            "summary": ""
                        }
                    },
                    "block_id": "5357c1f1-468c-4b2a-b0d4-03c1c58b8f3b",
                    "meta_info": []
                }
            ],
            "model_id": "DeepSeek-R1",
            "model_name": "DeepSeek-R1",
            "need_deep_think": 1,
        }
    ],
    "option": {
        "answer_with_suggest": false,
    },
    "chat_ability": {
        "ability_type": 6,
        "ability_param": "{\"action\":3}"
    },
    "ext": {
        "input_skill": "{\"skill_id\":\"6\",\"skill_type\":6,\"template_key\":\"query_with_reference\",\"variables\":{\"reference_text\":\"The O’Reilly logo is a registered trademark of O’Reilly Media, Inc. HandsOn Large Language Models, the cover image, and related trade dress are trademarks of O’Reilly Media, Inc.\"}}",
    }
}
```

## 接口响应的SSE事件数据结构如下：

id 表示索引顺序；
event 表示事件类型，包括：ACK， HEATBEAT， SYSTEM， CHAT_MESSAGE
data 表示事件数据，包括：
    event：事件类型，包括如下：
        - message_start：消息开始
        - node_start：节点开始
        - node_delta：节点增量
        - node_end：节点结束
        - content_delta：内容增量
        - suggestions：建议项输出（在目标 node_end 之后发送）
        - message_end：消息结束
        - DONE：响应结束

    response_id：响应ID， 表示某一次的响应
    message_id：消息ID，当前是哪一条消息
    node_id：节点ID，节点ID
    type：节点类型，包括：thinking， agent， tool
    title：节点标题
    delta：节点增量数据
    status：节点状态，包括：completed
    created：事件创建时间

```sse
id: 1
event: ack
data: {}

id: 2
event: heatbeat
data: {}

id: 3
event: system
data: {}

id: 4
event: chat_message
data: {"event":"message_start","response_id":"r1","message_id":"m1","created":1}

id: 5
event: chat_message
data: {"event":"node_start","response_id":"r1","message_id":"m1","node_id":"n1","type":"thinking","title":"思考","created":2}

id: 6
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n1","delta":"我需要检索'违规发行预付卡'相关的法律","created":3}

id: 7
event: chat_message
data: {"event":"node_end","response_id":"r1","message_id":"m1","node_id":"n1","status":"completed","title":"思考","created":4}

id: 8
event: chat_message
data: {"event":"node_start","response_id":"r1","message_id":"m1","node_id":"n2","type":"agent","title":"Agent⚙️ 法规检索进行中","created":5}

id: 9
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","node_id":"s1","type":"thinking","title":"任务规划","created":7}}

id: 10
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s1","delta":"检索目录 -> 阅读原文 -> 输出条款","created":8}}
id: 11
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","node_id":"s1","status":"completed","title":"任务规划","created":9}}

id: 12
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","node_id":"s2","type":"tool","title":"阅读目录","created":10}}
id: 13
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"阅读目录，分析可能相关法律...","created":11}}
id: 14
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"进度10%","ephemeral":"true","created":12}}
id: 15
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"进度50%","ephemeral":"true","created":13}}
id: 16
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s2","delta":"进度100%","ephemeral":"true","created":14}}

id: 17
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","node_id":"s2","status":"completed","title":"完成初步检索","created":15}}

id: 18
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","node_id":"s3","type":"tool","title":"阅读原文","created":16}}
id: 19
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s3","delta":"阅读法律原文，分析可能相关条款...","created":17}}
id: 20
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s3","delta":"进度10%","ephemeral":"true","created":18}}
id: 21
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","node_id":"s3","delta":"进度50%","ephemeral":"true","created":19}}
id: 22
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","node_id":"s3","status":"completed","title":"完成条款检索","created":18}}

id: 23
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_start","response_id":"r1","message_id":"m1-1","node_id":null,"index":0,"delta":"正在进行资料整理","role":"assistant","created":19}}
id: 24
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_delta","response_id":"r1","message_id":"m1-1","node_id":null,"index":0,"delta":"正在进行资料整理","role":"assistant","created":19}}
id: 25
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n2","delta":{"event":"node_end","response_id":"r1","message_id":"m1-1","node_id":null,"index":0,"delta":"检索到了7个法律条款","role":"assistant","created":19}}

id: 26
event: chat_message
data: {"event":"node_end","response_id":"r1","message_id":"m1","node_id":"n2","status":"completed","title":"Agent⚙️ 法规检索已完成","created":22}

id: 27
event: chat_message
data: {"event":"node_start","response_id":"r1","message_id":"m1","node_id":"n3","type":"thinking","title":"正在综合分析","created":23}

id: 28
event: chat_message
data: {"event":"node_delta","response_id":"r1","message_id":"m1","node_id":"n3","delta":"结合法规检索结果生成最终建议...","created":24}
id: 29
event: chat_message
data: {"event":"node_end","response_id":"r1","message_id":"m1","node_id":"n3","status":"completed","title":"综合分析完成","created":25}

id: 30
event: chat_message
data: {"event":"content_delta","response_id":"r1","message_id":"m1","node_id":null,"index":0,"delta":"违规发行预付卡违反了《大气污染防治法》第三条引用...","role":"assistant","created":26,"metadata":{"channel":"final"}}

id: 31
event: chat_message
data: {"event":"suggestions","message_id":"m1","node_id":"m1","conversation_id":"c1","response_id":"r1","created":27,"status":"completed","type":"suggestions","delta":"{\"suggestions\":[{\"tool_id\":\"complaint_analysis\",\"name\":\"分析举报事项\"},{\"tool_id\":\"export_flow\",\"name\":\"导出流水\"}]}"}

id: 31
event: chat_message
data: {"event":"message_end","response_id":"r1","message_id":"m1","created":27}

id: 32
event: DONE
data: {"event":"done","response_id":"r1","message_id":"m1","created":28}
```
