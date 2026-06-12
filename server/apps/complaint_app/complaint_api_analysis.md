# Request:

curl -N -X POST "http://127.0.0.1:5001/api/complaint/complaint-analysis" \
    -H "Accept: text/event-stream" \
    -F "user_id=api_user" \
    -F "use_uploaded_pdf=true" \
    -F "force=true" \
    -F "file=@/Users/fengyingsheng/Downloads/mock_举报信_5图片_交易流水.pdf;type=application/pdf"

curl -N -X POST "http://127.0.0.1:5001/api/complaint/complaint-analysis" \
    -H "Accept: text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"api_user","file_id":"YOUR_FILE_ID","force":false}'

curl -N -X POST "http://127.0.0.1:5001/api/complaint/complaint-analysis" \
    -H "Accept: text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"api_user","org_id":"ORG_001","entity_id":"ENTITY_001","reporter_name":"张三","scope":"entity","file_id":"YOUR_FILE_ID","force":false}'

curl -N -X POST "http://127.0.0.1:5001/api/complaint/complaint-analysis" \
    -H "Accept: text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"api_user","file_ids":["CASE_FILE_1.pdf","CASE_FILE_2.pdf"],"force":false}'

curl -N -X POST "http://127.0.0.1:5001/api/complaint/complaint-analysis" \
    -H "Accept: text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"api_user","file_id":"YOUR_FILE_ID","force":false,"match_strategy":"two_stage"}'

curl -N -X POST "http://127.0.0.1:5001/api/complaint/complaint-analysis" \
    -H "Accept: text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"user_id":"api_user","file_id":"1111","force":true}'

# Response:
data: {"task": "complaint_analysis", "status": "started", "message": "开始处理举报件", "progress": 0, "match_strategy": "two_stage", "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "正在生成摘要", "progress": 10, "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "正在提取举报事项与诉求", "progress": 30, "phase_node": "complaint_extract_items", "match_strategy": "two_stage", "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "已提取举报事项与诉求", "progress": 30, "phase_node": "complaint_extract_items", "match_strategy": "two_stage", "phase_result": {"complaint_profile": {"reporter": "王志武", "report_date": "", "reported_institution": "招商银行信用卡中心", "title_base": ""}, "items_and_appeals": {"complaint_items": [{"id": "complaint_1", "label": "未履行审慎发卡义务", "content": "招商银行信用卡中心未依规核实王志武有效身份、稳定收入来源及还款能力，违反《商业银行信用卡业务监督管理办法》第四十条、第四十三条第（四）款，涉嫌违背审慎经营规则。"}], "appeals": []}}, "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "正在判断举报属于哪类问题（如商户、扣款、账户）", "progress": 50, "phase_node": "complaint_match_experience_stage1", "match_strategy": "two_stage", "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "正在细化具体违规情形并补充法律依据", "progress": 60, "phase_node": "complaint_match_experience_stage2", "match_strategy": "two_stage", "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "已完成经验匹配并补强法律依据", "progress": 60, "phase_node": "complaint_match_experience", "match_strategy": "two_stage", "phase_result": {"matched_items": [{"complaint_id": "complaint_1", "complaint": "招商银行信用卡中心未依规核实王志武有效身份、稳定收入来源及还款能力，违反《商业银行信用卡业务监督管理办法》第四十条、第四十三条第（四）款，涉嫌违背审慎经营规则。", "matched_categories": [{"level1": "银行开户管理不到位", "level2": "未有效履行客户身份识别义务", "is_payment_scope": "是", "check_points": ["客户身份识别方式、分级分类、是否代理开户"], "document_checklist": ["调阅清单"]}]}]}, "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "正在生成核实思路", "progress": 80, "phase_node": "complaint_final_conclusion", "match_strategy": "two_stage", "base_id": "a79100eeba420af1"}

data: {"task": "complaint_analysis", "status": "processing", "message": "已生成核实思路", "progress": 80, "phase_node": "complaint_final_conclusion", "match_strategy": "two_stage", "phase_result": {"conclusion_text": "根据您提供的举报内容，经分析，现将举报事项分析结果报告如下：..."}, "base_id": "a79100eeba420af1"}

data: {
  "task": "complaint_analysis",
  "status": "completed",
  "message": "生成完成",
  "progress": 100,
  "result": {
    "api_kind": "complaint_analysis",
    "base_id": "a79100eeba420af1",
    "complaint_profile": {
      "reporter": "王志武",
      "report_date": "",
      "reported_institution": "招商银行信用卡中心",
      "title_base": ""
    },
    "items_and_appeals": {
      "complaint_items": [
        {
          "id": "complaint_1",
          "label": "未履行审慎发卡义务",
          "content": "招商银行信用卡中心未依规核实王志武有效身份、稳定收入来源及还款能力，违反《商业银行信用卡业务监督管理办法》第四十条、第四十三条第（四）款，涉嫌违背审慎经营规则。"
        },
        {
          "id": "complaint_2",
          "label": "违规超规模授信",
          "content": "招商银行向月收入1500元的王志武核发3000元信用卡额度，明显超出其还款能力，涉嫌违规超限授信并据此收取违约金和利息。"
        },
        {
          "id": "complaint_3",
          "label": "未尽交易监测与风险提示义务",
          "content": "招商银行对王志武账户发生的大额套现、虚假商户交易未开展有效监测与风险提示，未履行反欺诈及金融消费者权益保护法定职责。"
        }
      ],
      "appeals": []
    },
    "law_basis": [],
    "document_checklist": [
      {
        "category": "银行开户管理不到位/未有效履行客户身份识别义务",
        "items": [
          "调阅清单"
        ],
        "source": "expert"
      },
      {
        "category": "风险监测不到位/2、可疑交易报告后续控制措施不到位",
        "items": [
          "调阅清单"
        ],
        "source": "expert"
      },
      {
        "category": "转账管理不到位/3、频繁向陌生账号大额转账，付款银行未及时预警止付。",
        "items": [
          "调阅清单"
        ],
        "source": "expert"
      },
      {
        "category": "风险监测不到位/1、收款账户收到大额进账后短时间内分散转出，符合快进快出特点，收款银行未及时止付。",
        "items": [
          "调阅清单"
        ],
        "source": "expert"
      }
    ],
    "verification_points": [
      {
        "category": "银行开户管理不到位/未有效履行客户身份识别义务",
        "point": "客户身份识别方式、分级分类、是否代理开户",
        "source": "expert"
      },
      {
        "category": "风险监测不到位/1、收款账户收到大额进账后短时间内分散转出，符合快进快出特点，收款银行未及时止付。",
        "point": "是否符合快进快出明显特征、是否出发行内风险监测系统、触发后是否进行核实（无时限要求）、核实的方式、才去的管控措施",
        "source": "expert"
      }
    ],
    "conclusion_text": "根据您提供的举报内容，经分析，现将举报事项分析结果报告如下：\n\n### **一、举报事项：未履行审慎发卡义务**\n**举报事项文本**：招商银行信用卡中心未依规核实王志武有效身份、稳定收入来源及还款能力，违反《商业银行信用卡业务监督管理办法》第四十条、第四十三条第（四）款，涉嫌违背审慎经营规则。\n\n**专家经验匹配**：已找到。\n**是否属于支付部门核实范围**：是\n**核实事项**：招商银行信用卡中心未依规核实王志武有效身份、稳定收入来源及还款能力，违反《商业银行信用卡业务监督管理办法》第四十条、第四十三条第（四）款，涉嫌违背审慎经营规则。\n**核实要点**：\n1.  核查银行在客户王志武申办信用卡时，采取何种方式进行客户身份识别。\n2.  核查银行是否对客户进行了分级分类管理。\n3.  核查本次信用卡申办是否为代理开户。\n**调阅清单**：调阅清单\n**处理建议**：建议重点核查招商银行在为客户王志武办理信用卡开户业务时，是否严格执行了客户身份识别制度。应调阅其开户申请资料、资信审核记录等，核实银行是否有效核验了客户身份证明文件的真实性、记录了其职业、收入等财务状况，并评估其还款能力，以判断银行是否履行了审慎发卡的义务。同时，关注其在监测到账户出现大额套现、虚假商户交易等可疑行为时，是否采取了必要的风险提示或控制措施。\n**法律法规**：未检索到相关法律条文。\n\n### **二、举报事项：违规超规模授信**\n**举报事项文本**：招商银行向月收入1500元的王志武核发3000元信用卡额度，明显超出其还款能力，涉嫌违规超限授信并据此收取违约金和利息。\n\n**专家经验匹配**：已找到。\n**是否属于支付部门核实范围**：是\n**核实事项**：招商银行向月收入1500元的王志武核发3000元信用卡额度，明显超出其还款能力，涉嫌违规超限授信并据此收取违约金和利息。\n**核实要点**：\n1.  核查银行在客户王志武申办信用卡时，采取何种方式进行客户身份识别。\n2.  核查银行是否对客户进行了分级分类管理。\n3.  核查本次信用卡申办是否为代理开户。\n4.  核查相关交易是否符合“快进快出”的明显特征。\n5.  核查相关交易是否触发了银行内部的风险监测系统。\n6.  核查触发风险监测后，银行是否进行了核实（无时限要求）。\n7.  核查银行进行核实所采取的方式。\n8.  核查银行最终采取了何种管控措施。\n**调阅清单**：调阅清单\n**处理建议**：建议将授信额度审批的合理性作为核查重点。需核实银行在审批王志武信用卡额度时，是否基于其提供的月收入1500元等财务状况进行了审慎评估，授信额度（3000元）的核定逻辑和依据是否符合监管关于还款能力评估的要求。此事项与“未履行审慎发卡义务”关联紧密，可一并核查其客户身份识别与资信调查流程的完整性。\n**法律法规**：未检索到相关法律条文。\n\n### **三、举报事项：未尽交易监测与风险提示义务**\n**举报事项文本**：招商银行对王志武账户发生的大额套现、虚假商户交易未开展有效监测与风险提示，未履行反欺诈及金融消费者权益保护法定职责。\n\n**专家经验匹配**：已找到。\n**是否属于支付部门核实范围**：是\n**核实事项**：招商银行对王志武账户发生的大额套现、虚假商户交易未开展有效监测与风险提示，未履行反欺诈及金融消费者权益保护法定职责。\n**核实要点**：\n1.  核查银行在客户王志武申办信用卡时，采取何种方式进行客户身份识别。\n2.  核查银行是否对客户进行了分级分类管理。\n3.  核查本次信用卡申办是否为代理开户。\n**调阅清单**：调阅清单\n**处理建议**：建议核查招商银行对王志武信用卡账户的交易监控情况。应调阅该账户的历史交易流水，特别是涉及举报所称的“大额套现、虚假商户交易”的记录，并核查银行的风险监测系统是否对这些异常交易产生预警。同时，需核实银行在监测到可疑交易后，是否按照内部规定及监管要求，采取了包括风险提示在内的后续控制措施，以评估其交易监测机制的有效性。\n**法律法规**：未检索到相关法律条文。",
    "versions": {
      "text_version": 5,
      "summary_version": 5,
      "analysis_version": 0,
      "classification_version": 0
    }
  },
  "issue_analysis_version": 5,
  "cache_hit": false,
  "match_strategy": "two_stage",
  "versions": {
    "text": 5,
    "summary": 5,
    "issue_analysis": 5
  },
  "base_id": "a79100eeba420af1"
}

# SSE 事件结构

- 固定字段：`task`、`status`、`message`、`progress`、`base_id`
- 可选字段：`match_strategy`、`scope_context`、`phase_node`、`phase_result`、`result`、`issue_analysis_version`、`cache_hit`、`versions`、`error`
- 当某个阶段已经产出可供前端展示的中间结果时，会在 `processing` 事件中附带 `phase_result`

# scope 说明

- `user_id` 仍必填，但只作为操作者写入 `meta` 和日志，不再作为新接口主存储路径。
- `scope` 可选，支持 `default`、`org`、`entity`；不传时服务端按 `org_id/entity_id` 自动推断，均不传则为 `default`。
- `org` scope 必须传 `org_id`；`entity` scope 必须同时传 `org_id` 和稳定的 `entity_id`。
- 新路径为 `DATA_ROOT/complaints/<base_id>/<scope_path>/...`，其中 `scope_path` 为 `default`、`<org_id>`、`<org_id>/<entity_id>`；传入 `reporter_name` 时追加举报人姓名，例如 `<org_id>/<entity_id>/<reporter_name>`。
- `original`、`texts`、`summary` 默认写入 `default`；`issue_analysis` 写入当前 scope。
- 读取覆盖层时按 `entity -> org -> default` 回退；写入不做隐式回退。
- 当前版本只校验 `org_id/entity_id` 路径安全，不内置组织权限判定；调用方需在进入接口前完成授权。

`phase_result` 当前仅在以下节点返回：
- `complaint_extract_items`：返回 `complaint_profile` 与 `items_and_appeals`
- `complaint_match_experience`：返回精简后的 `matched_items`
- `complaint_final_conclusion`：返回 `conclusion_text`

# 进度口径（当前实现）

- `10%`：正在生成摘要
- `30%`：正在提取举报事项与诉求
- `50%`：正在判断举报属于哪类问题（如商户、扣款、账户，仅 `match_strategy=two_stage`）
- `60%`：正在细化具体违规情形并补充法律依据（仅 `match_strategy=two_stage`）
- `60%`：正在匹配专家经验并补强法律依据（`match_strategy=legacy`）
- `80%`：正在生成核实思路
- `100%`：生成完成

# match_strategy 说明

- 可选值：`two_stage`、`legacy`、`law_skill`
- 默认值：`two_stage`（由服务端配置 `COMPLAINT_MATCH_STRATEGY_DEFAULT` 控制）
- `law_skill`：逐个举报事项调用本地法规库 skill，基于用户提及法律、证据材料和本地 `laws` 目录生成法律适用性分析、possible_laws、核查点与调阅清单。
