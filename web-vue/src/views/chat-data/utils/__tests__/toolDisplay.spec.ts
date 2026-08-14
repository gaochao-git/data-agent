import { describe, expect, it } from 'vitest'

import { getToolCompactDescription, getToolDisplayName } from '../toolDisplay'

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
      file_glob: '查找文件',
      file_grep: '搜索文本',
      dir_create: '创建目录',
      dir_list: '列出目录',
      dir_delete: '删除目录',
      present_artifacts: '展示文件',
      get_current_time: '获取当前时间',
      semantic_search: '语义检索',
      search_dataset: '检索语义模型',
      dsl2sql_tool: '编译 SQL',
      semantic_query_tool: '查询数据',
      calculator_tool: '精确计算',
      bash_run: '运行脚本',
      officecli_run: '运行 OfficeCLI',
      ask_user_question: '询问用户',
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

  it('extracts the primary argument shown after compact tool names', () => {
    expect(getToolCompactDescription('search_dataset', { query: '产品分类模型' })).toBe('产品分类模型')
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
    expect(getToolCompactDescription('semantic_search', {
      operation: 'search_models',
      query: '查询订单收入相关模型',
    })).toBe('查询订单收入相关模型')
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
    expect(getToolCompactDescription('unknown_tool', { query: '不展示' })).toBe('')
  })
})
