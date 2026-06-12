[
  {
    message_id: "c-1",
    role: "user",
    text: "帮我分析举报件，附件是****.png",
    attachment: {
      id: "complaint_001:file_20251210",
      name: "2025-12-10.png",
    },
    time: "17:54:20",
  },
  {
    message_id: "c-2",
    role: "assistant",
    time: "17:54:30",
    steps: [
      {
        id: "thinking",
        label: "思考已完成",
        status: "done",
        detail:
          "法人需要注意的3项关键事项总结如下：\n1、打算看很费劲哪思考打发时间的看法哈开始给他风华卡就很少有风景中来电话费卡健身房看见",
      },
      {
        id: "tool",
        label: "工具调用已完成",
        status: "done",
        children: [
          {
            id: "thinking-sub-1",
            label: "规划完成",
            status: "done",
            detail:
              "法人需要注意的3项关键事项总结如下：\n1、打算看很费劲哪思考打发时间的看法哈开始给他风华卡就很少有风景中来电话费卡健身房看见",
          },
          {
            id: "thinking-sub-2",
            label: "信息抽取完成",
            status: "done",
            detail:
              "法人需要注意的3项关键事项总结如下：\n2、打算看很费劲哪思考打发时间的看法哈开始给他风华卡就很少有风景中来电话费卡健身房看见",
          },
        ],
      },
      { id: "summary", label: "结论生成中", status: "thinking", detail: "生成中..." },
    ],
    content:
      "打算看举报邮件的时间与内容开始始终化代风华卡航班很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡。",
    suggestions: [
      { tool_id: "complaint_analysis", name: "分析举报事项" },
      { tool_id: "export_flow", name: "导出流水" },
    ],
  },
]
