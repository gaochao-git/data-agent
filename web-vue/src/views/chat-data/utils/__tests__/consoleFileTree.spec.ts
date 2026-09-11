import { describe, expect, it } from 'vitest'

import {
  collapseAllDirectories,
  collectCollapsibleDirectoryPaths,
  expandAllDirectories,
  isTreeFullyCollapsed,
  type FileTreeCollapseNode,
} from '../consoleFileTree'

const file = (path: string): FileTreeCollapseNode => ({ path, children: [], file: {} })

// 一份贴近真实控制台的结构：uploads 下只有文件、workspace 下有一层子目录、outputs 为空目录
const TREE: FileTreeCollapseNode[] = [
  { path: 'uploads', children: [file('uploads/明细.xlsx')] },
  {
    path: 'workspace',
    children: [
      {
        path: 'workspace/scripts',
        children: [file('workspace/scripts/build.py')],
      },
    ],
  },
  { path: 'outputs', children: [] },
]

describe('collectCollapsibleDirectoryPaths', () => {
  it('收集所有含子节点的目录，递归到最深一层', () => {
    expect(collectCollapsibleDirectoryPaths(TREE)).toEqual(['uploads', 'workspace', 'workspace/scripts'])
  })

  it('跳过文件节点与空目录', () => {
    const paths = collectCollapsibleDirectoryPaths(TREE)
    expect(paths).not.toContain('uploads/明细.xlsx')
    expect(paths).not.toContain('outputs')
  })

  it('带 children 的文件节点仍不算目录（防御：建树目前不会产出这种形状）', () => {
    const weird: FileTreeCollapseNode[] = [
      { path: 'outputs', children: [{ path: 'outputs/报告.md', children: [file('outputs/报告.md/子项')], file: {} }] },
    ]
    expect(collectCollapsibleDirectoryPaths(weird)).toEqual(['outputs'])
  })

  it('空树返回空数组', () => {
    expect(collectCollapsibleDirectoryPaths([])).toEqual([])
  })
})

describe('collapseAllDirectories / expandAllDirectories', () => {
  const paths = collectCollapsibleDirectoryPaths(TREE)

  it('全部折叠把所有目录路径并入集合', () => {
    const collapsed = collapseAllDirectories(paths, new Set())
    expect([...collapsed].sort()).toEqual(['uploads', 'workspace', 'workspace/scripts'])
  })

  it('全部折叠保留集合里原有的默认折叠项', () => {
    // 首次进入时默认折叠 uploads，其余展开；'legacy' 是当前树里没有的条目，也应原样保留
    const collapsed = collapseAllDirectories(paths, new Set(['uploads', 'legacy']))
    expect([...collapsed].sort()).toEqual(['legacy', 'uploads', 'workspace', 'workspace/scripts'])
  })

  it('全部展开只移除本棵树的路径，集合里其它条目原样保留', () => {
    const collapsed = expandAllDirectories(paths, new Set(['uploads', 'workspace', 'workspace/scripts', 'legacy']))
    expect([...collapsed]).toEqual(['legacy'])
  })

  it('不修改传入的集合（返回新 Set）', () => {
    const source = new Set<string>(['uploads'])
    const collapsed = collapseAllDirectories(paths, source)
    const expanded = expandAllDirectories(paths, source)
    expect([...source]).toEqual(['uploads'])
    expect(collapsed).not.toBe(source)
    expect(expanded).not.toBe(source)
  })
})

describe('isTreeFullyCollapsed', () => {
  const paths = collectCollapsibleDirectoryPaths(TREE)

  it('没有可折叠目录时返回 false（空列表不显示「展开全部」）', () => {
    expect(isTreeFullyCollapsed([], new Set())).toBe(false)
  })

  it('全部目录都在折叠集合里才算全部折叠', () => {
    expect(isTreeFullyCollapsed(paths, new Set(paths))).toBe(true)
    expect(isTreeFullyCollapsed(paths, new Set(['uploads', 'workspace']))).toBe(false)
  })
})

describe('一键折叠/展开的按钮状态流转', () => {
  it('折叠后判定为已全部折叠，展开后恢复为未全部折叠', () => {
    const paths = collectCollapsibleDirectoryPaths(TREE)
    let collapsed: ReadonlySet<string> = new Set(['uploads'])
    expect(isTreeFullyCollapsed(paths, collapsed)).toBe(false)

    collapsed = collapseAllDirectories(paths, collapsed)
    expect(isTreeFullyCollapsed(paths, collapsed)).toBe(true)

    collapsed = expandAllDirectories(paths, collapsed)
    expect(isTreeFullyCollapsed(paths, collapsed)).toBe(false)
    // 展开后连默认折叠的 uploads 也一并打开，用户点的是「展开全部」
    expect([...collapsed]).toEqual([])
  })
})
