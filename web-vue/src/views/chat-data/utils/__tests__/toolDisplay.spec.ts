import { describe, expect, it } from 'vitest'

import { extractWrittenFilePathCandidates, getDataAnalyzingActionName, getToolCompactDescription, getToolDisplayName, getToolTechnicalDescription, isFileDiffOffloaded, splitFileDiffLines } from '../toolDisplay'

describe('toolDisplay', () => {
  it('maps every current ChatData tool to a user-facing name', () => {
    const expectedNames: Record<string, string> = {
      file_read: '读取文件',
      file_edit: '修改文件',
      file_write: '写入文件',
      file_move: '移动文件',
      file_copy: '复制文件',
      file_delete: '删除文件',
      parse_document: '解析文档',
      read_image: '读取图片',
      file_glob: '查找文件',
      file_grep: '搜索文本',
      dir_create: '创建目录',
      dir_list: '列出目录',
      dir_delete: '删除目录',
      present_artifacts: '展示文件',
      get_current_time: '获取当前时间',
      data_analyzing_tool: '数据分析',
      calculator_tool: '精确计算',
      bash_run: '运行脚本',
      officecli_run: '运行 OfficeCLI',
      ask_user_question: '询问用户',
      write_todos: '更新任务计划',
      task: '派发子智能体',
    }

    for (const [toolName, displayName] of Object.entries(expectedNames)) {
      expect(getToolDisplayName(toolName), toolName).toBe(displayName)
    }
  })

  it('keeps the generic fallback for unknown or empty tool names', () => {
    expect(getToolDisplayName('unknown_tool')).toBe('工具调用')
    expect(getToolDisplayName('semantic_tool')).toBe('工具调用')
    expect(getToolDisplayName('  ')).toBe('工具调用')
    expect(getToolDisplayName(null)).toBe('工具调用')
  })

  it('passes MCP qualified names through without local mapping', () => {
    // 展示名由后端 tool_labels 下发（display_name），前端不维护 MCP 映射表
    expect(getToolDisplayName('mcp_fault-ssh_system_overview')).toBe('mcp_fault-ssh_system_overview')
  })

  it('extracts the primary argument shown after compact tool names', () => {
    expect(getToolCompactDescription('file_write', {
      file_path: '/mnt/user-data/outputs/语义模型分析报告.md',
    })).toBe('/mnt/user-data/outputs/语义模型分析报告.md')
    expect(getToolCompactDescription('file_read', { path: String.raw`workspace\reports\a.md` })).toBe('workspace/reports/a.md')
    expect(getToolCompactDescription('file_delete', {
      file_path: '/mnt/user-data/workspace/临时文件.md',
    })).toBe('/mnt/user-data/workspace/临时文件.md')
    expect(getToolCompactDescription('file_move', {
      source_path: 'workspace/草稿.md',
      target_path: 'outputs/报告.md',
    })).toBe('workspace/草稿.md -> outputs/报告.md')
    expect(getToolCompactDescription('file_glob', {
      path: 'workspace/reports',
      pattern: '*.md',
    })).toBe('workspace/reports · *.md')
    expect(getToolCompactDescription('file_grep', {
      path: 'workspace/reports',
      pattern: '销售额|订单量',
    })).toBe('workspace/reports · 销售额|订单量')
    expect(getToolCompactDescription('dir_delete', { path: 'workspace/temp' })).toBe('workspace/temp')
    expect(getToolCompactDescription('parse_document', {
      source_path: '/mnt/user-data/uploads/报告.docx',
      target_path: '/mnt/user-data/workspace/报告.md',
    })).toBe('/mnt/user-data/uploads/报告.docx -> /mnt/user-data/workspace/报告.md')
    expect(getToolCompactDescription('present_artifacts', {
      files: ['/mnt/user-data/outputs/报告.md'],
    })).toBe('/mnt/user-data/outputs/报告.md')
    expect(getToolCompactDescription('present_artifacts', {
      files: ['/mnt/user-data/outputs/报告.md', '/mnt/user-data/outputs/明细.xlsx'],
    })).toBe('/mnt/user-data/outputs/报告.md 等 2 个文件')
    expect(getToolCompactDescription('data_analyzing_tool', {
      action: 'execute_query',
      action_payload: { save_as: 'outputs/结果.json' },
    })).toBe('outputs/结果.json')
    expect(getToolCompactDescription('calculator_tool', {
      calculations: [
        { name: '已支付占比', expression: '205/235*100' },
        { name: '待处理占比', expression: '17/235*100' },
      ],
    })).toBe('已支付占比 等 2 项')
    expect(getToolCompactDescription('officecli_run', {
      command_args: ['view', '/mnt/user-data/workspace/报告.docx', 'outline'],
    })).toBe('view /mnt/user-data/workspace/报告.docx outline')
    expect(getToolCompactDescription('bash_run', {
      command: 'python3 scripts/build_report.py\n--output outputs/report.md',
    })).toBe('python3 scripts/build_report.py --output outputs/report.md')
    expect(getToolCompactDescription('ask_user_question', {
      questions: [{ question: '请选择统计范围' }],
    })).toBe('请选择统计范围')
    expect(getToolCompactDescription('write_todos', {
      todos: [
        { content: '读取数据', status: 'completed' },
        { content: '生成图表并核对数值', status: 'in_progress' },
        { content: '输出报告', status: 'pending' },
      ],
    })).toBe('共 3 项，已完成 1，进行中：生成图表并核对数值')
    expect(getToolCompactDescription('write_todos', {
      todos: [{ content: '唯一步骤', status: 'pending' }],
    })).toBe('共 1 项，已完成 0')
    expect(getToolCompactDescription('read_image', {
      file_path: '/mnt/user-data/uploads/登录页面.png',
    })).toBe('/mnt/user-data/uploads/登录页面.png')
    expect(getToolCompactDescription('task', {
      subagent_type: 'data-explore',
      description: '分析近 30 天入金金额趋势并给出结论',
    })).toBe('data-explore · 分析近 30 天入金金额趋势并给出结论')
    expect(getToolCompactDescription('task', {
      description: '只给了任务书没有类型',
    })).toBe('只给了任务书没有类型')
    expect(getToolCompactDescription('unknown_tool', { query: '不展示' })).toBe('')
  })

  it('prefers the model-provided intent over the args-derived technical description', () => {
    expect(getToolCompactDescription('officecli_run', {
      intent: '为月报刷标准报告表格样式',
      command_args: ['batch', '/mnt/user-data/workspace/月报.docx', '--file', 'styles.json'],
    })).toBe('为月报刷标准报告表格样式')
    // 无 intent 时回退到技术细节，行为不变
    expect(getToolCompactDescription('officecli_run', {
      command_args: ['view', '/mnt/user-data/workspace/报告.docx'],
    })).toBe('view /mnt/user-data/workspace/报告.docx')
    // intent 为空白/非字符串时忽略
    expect(getToolCompactDescription('file_read', { intent: '   ', file_path: 'a.md' })).toBe('a.md')
    expect(getToolCompactDescription('file_read', { intent: 123, file_path: 'a.md' })).toBe('123')
    // 超长 intent 截断
    const longIntent = '长'.repeat(100)
    expect(getToolCompactDescription('file_read', { intent: longIntent, file_path: 'a.md' })).toBe(
      `${'长'.repeat(80)}...`
    )
  })

  it('keeps the technical description independent of intent for hover display', () => {
    const args = {
      intent: '为月报刷标准报告表格样式',
      command_args: ['batch', '/mnt/user-data/workspace/月报.docx'],
    }
    expect(getToolTechnicalDescription('officecli_run', args)).toBe(
      'batch /mnt/user-data/workspace/月报.docx'
    )
    expect(getToolTechnicalDescription('unknown_tool', { intent: 'x' })).toBe('')
  })

  it('maps data_analyzing actions to Chinese names and leaves others empty', () => {
    expect(getDataAnalyzingActionName('check_yaml')).toBe('语义模型自检')
    expect(getDataAnalyzingActionName('execute_query')).toBe('查询数据')
    expect(getDataAnalyzingActionName('unknown_action')).toBe('')
    expect(getDataAnalyzingActionName(null)).toBe('')
  })
})

describe('splitFileDiffLines', () => {
  it('classifies header, hunk, add, del and context lines', () => {
    const lines = splitFileDiffLines([
      '--- /a.txt',
      '+++ /b.txt',
      '@@ -1,2 +1,3 @@',
      ' context',
      '+added',
      '-removed',
    ].join('\n'))
    expect(lines.map(l => l.type)).toEqual(['meta', 'meta', 'hunk', 'context', 'add', 'del'])
  })

  it('computes old/new line numbers from hunk headers', () => {
    const lines = splitFileDiffLines([
      '--- /a.txt',
      '+++ /b.txt',
      '@@ -1,2 +1,3 @@',
      ' context',
      '+added',
      '-removed',
      ' tail',
    ].join('\n'))
    expect(lines[2]).toEqual({ type: 'hunk', content: '@@ -1,2 +1,3 @@' })
    expect(lines[3]).toEqual({ type: 'context', content: ' context', oldNo: 1, newNo: 1 })
    expect(lines[4]).toEqual({ type: 'add', content: '+added', newNo: 2 })
    expect(lines[5]).toEqual({ type: 'del', content: '-removed', oldNo: 2 })
    expect(lines[6]).toEqual({ type: 'context', content: ' tail', oldNo: 3, newNo: 3 })
  })

  it('resets numbering at each hunk header', () => {
    const lines = splitFileDiffLines([
      '@@ -10,2 +10,2 @@',
      ' ctx',
      '@@ -50,1 +60,1 @@',
      '+new',
    ].join('\n'))
    expect(lines[1]).toEqual({ type: 'context', content: ' ctx', oldNo: 10, newNo: 10 })
    expect(lines[3]).toEqual({ type: 'add', content: '+new', newNo: 60 })
  })

  it('treats ---/+++ lines inside hunk body as del/add, not header', () => {
    const lines = splitFileDiffLines([
      '--- /a.txt',
      '+++ /b.txt',
      '@@ -1,3 +1,3 @@',
      '---正文里以减号开头的删除行',
      '+++正文里以加号开头的新增行',
      '@@ -5,1 +6,1 @@',
      ' context',
    ].join('\n'))
    expect(lines[3]).toEqual({ type: 'del', content: '---正文里以减号开头的删除行', oldNo: 1 })
    expect(lines[4]).toEqual({ type: 'add', content: '+++正文里以加号开头的新增行', newNo: 1 })
    expect(lines[5]).toEqual({ type: 'hunk', content: '@@ -5,1 +6,1 @@' })
    expect(lines[6]).toEqual({ type: 'context', content: ' context', oldNo: 5, newNo: 6 })
  })
})

describe('isFileDiffOffloaded', () => {
  it('detects offloaded file_diff results only', () => {
    expect(isFileDiffOffloaded('file_diff', 'completed', '[tool_result_offloaded]\nsource_tool=file_diff')).toBe(true)
    expect(isFileDiffOffloaded('file_diff', 'completed', '[file_diff]\n{"status":"changed"}')).toBe(false)
    expect(isFileDiffOffloaded('file_read', 'completed', '[tool_result_offloaded]')).toBe(false)
    expect(isFileDiffOffloaded('file_diff', 'running', '[tool_result_offloaded]')).toBe(false)
  })
})

describe('extractWrittenFilePathCandidates', () => {
  it('collects path keys from file tools (file_path/target_path/destination_path/path)', () => {
    expect(extractWrittenFilePathCandidates({
      file_path: '/mnt/user-data/outputs/a.md',
      target_path: '/mnt/user-data/outputs/b.md',
      destination_path: '/mnt/user-data/outputs/c.md',
      path: '/mnt/user-data/workspace/d.md',
    })).toEqual([
      '/mnt/user-data/outputs/a.md',
      '/mnt/user-data/outputs/b.md',
      '/mnt/user-data/outputs/c.md',
      '/mnt/user-data/workspace/d.md',
    ])
  })

  it('reads save_as from data_analyzing execute_query action_payload', () => {
    expect(extractWrittenFilePathCandidates({
      action: 'execute_query',
      action_payload: { sql: 'SELECT 1', save_as: '/mnt/user-data/outputs/结果.csv' },
    })).toEqual(['/mnt/user-data/outputs/结果.csv'])
  })

  it('expands present_artifacts files list', () => {
    expect(extractWrittenFilePathCandidates({
      files: ['/mnt/user-data/outputs/报告.docx', '/mnt/user-data/outputs/图.png'],
      intent: '展示产物',
    })).toEqual(['/mnt/user-data/outputs/报告.docx', '/mnt/user-data/outputs/图.png'])
  })

  it('skips non-string and blank values, returns empty for read-only tools', () => {
    expect(extractWrittenFilePathCandidates({
      command: 'echo hi',
      file_path: '   ',
      files: [123, null, ''],
      action_payload: { save_as: 42 },
    })).toEqual([])
    expect(extractWrittenFilePathCandidates({})).toEqual([])
  })
})
