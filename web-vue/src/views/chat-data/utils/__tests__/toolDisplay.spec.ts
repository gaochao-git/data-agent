import { describe, expect, it } from 'vitest'

import { getToolDisplayName } from '../toolDisplay'

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
      search_dataset: '搜索语义模型',
      dsl2sql_tool: '编译 SQL',
      semantic_query_tool: '查询数据',
      calculator_tool: '精确计算',
      bash_run: '运行脚本',
      officecli_run: '运行 OfficeCLI',
    }

    for (const [toolName, displayName] of Object.entries(expectedNames)) {
      expect(getToolDisplayName(toolName), toolName).toBe(displayName)
    }
  })

  it('keeps the generic fallback for unknown or empty tool names', () => {
    expect(getToolDisplayName('unknown_tool')).toBe('工具调用')
    expect(getToolDisplayName('select_semantic_config_tool')).toBe('工具调用')
    expect(getToolDisplayName('  ')).toBe('工具调用')
    expect(getToolDisplayName(null)).toBe('工具调用')
  })
})
