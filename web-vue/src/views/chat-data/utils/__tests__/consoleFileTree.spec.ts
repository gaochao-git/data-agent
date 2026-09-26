import { describe, expect, it } from 'vitest'

import {
  collapseAllDirectories,
  collectCollapsibleDirectoryPaths,
  DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES,
  expandAllDirectories,
  formatFileSize,
  isTreeFullyCollapsed,
  isWorkspaceDirectoryArchiveDownloadable,
  isWorkspaceFileDeletable,
  isWorkspaceFileDownloadable,
  syncTruncatedTitle,
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

describe('DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES', () => {
  it('默认收起 uploads 与 workspace', () => {
    expect([...DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES]).toEqual(['uploads', 'workspace'])
  })

  it('outputs 默认展开——结果目录一打开面板就该可见', () => {
    expect(DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES).not.toContain('outputs')
  })

  it('只含顶层目录名，父级展开后子目录照常可见', () => {
    expect(DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES.every((path) => !path.includes('/'))).toBe(true)
  })

  it('outputs 下有子目录时，默认态不会被判成「已全部折叠」', () => {
    const tree: FileTreeCollapseNode[] = [
      ...TREE.filter((node) => node.path !== 'outputs'),
      {
        path: 'outputs',
        children: [{ path: 'outputs/月报', children: [file('outputs/月报/11月经营月报.docx')] }],
      },
    ]
    const paths = collectCollapsibleDirectoryPaths(tree)
    expect(isTreeFullyCollapsed(paths, new Set(DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES))).toBe(false)
  })
})

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

describe('isWorkspaceFileDownloadable（SQL 查询结果不允许下载）', () => {
  it('uploads / workspace / outputs 下的普通文件都可下载', () => {
    expect(isWorkspaceFileDownloadable('uploads/明细.xlsx')).toBe(true)
    expect(isWorkspaceFileDownloadable('workspace/月报/2025年11月经营月报.docx')).toBe(true)
    expect(isWorkspaceFileDownloadable('outputs/经营月报.docx')).toBe(true)
  })

  it('SQL 查询结果与工具卸载缓存不可下载', () => {
    expect(isWorkspaceFileDownloadable('outputs/query_result/abc.csv')).toBe(false)
    expect(isWorkspaceFileDownloadable('outputs/query_result')).toBe(false)
    expect(isWorkspaceFileDownloadable('outputs/.tool-offload/abc.json')).toBe(false)
  })

  it('目录名只是前缀相似的不受影响（不是子路径就不是同一目录）', () => {
    expect(isWorkspaceFileDownloadable('outputs/query_result_backup/abc.csv')).toBe(true)
    expect(isWorkspaceFileDownloadable('outputs/query_results/abc.csv')).toBe(true)
  })

  it('规范化带沙箱前缀与 Windows 分隔符的路径', () => {
    expect(isWorkspaceFileDownloadable('/mnt/user-data/outputs/query_result/abc.csv')).toBe(false)
    expect(isWorkspaceFileDownloadable('\\outputs\\query_result\\abc.csv')).toBe(false)
    expect(isWorkspaceFileDownloadable('/mnt/user-data/outputs/经营月报.docx')).toBe(true)
  })

  it('空路径与缺省值一律不可下载（拿不到真实路径时不给入口）', () => {
    expect(isWorkspaceFileDownloadable('')).toBe(false)
    expect(isWorkspaceFileDownloadable('   ')).toBe(false)
    expect(isWorkspaceFileDownloadable(null)).toBe(false)
    expect(isWorkspaceFileDownloadable(undefined)).toBe(false)
  })
})

describe('isWorkspaceFileDeletable（查询结果需留存审计）', () => {
  it('uploads / workspace / outputs 下的普通文件都可删除', () => {
    expect(isWorkspaceFileDeletable('uploads/明细.xlsx')).toBe(true)
    expect(isWorkspaceFileDeletable('workspace/月报/2025年11月经营月报.docx')).toBe(true)
    expect(isWorkspaceFileDeletable('outputs/经营月报.docx')).toBe(true)
  })

  it('SQL 查询结果与工具卸载缓存不可删除', () => {
    expect(isWorkspaceFileDeletable('outputs/query_result/abc.csv')).toBe(false)
    expect(isWorkspaceFileDeletable('outputs/query_result')).toBe(false)
    expect(isWorkspaceFileDeletable('outputs/.tool-offload/abc.json')).toBe(false)
  })

  it('目录名只是前缀相似的不受影响', () => {
    expect(isWorkspaceFileDeletable('outputs/query_result_backup/abc.csv')).toBe(true)
    expect(isWorkspaceFileDeletable('outputs/query_results/abc.csv')).toBe(true)
  })

  it('规范化带沙箱前缀与 Windows 分隔符的路径', () => {
    expect(isWorkspaceFileDeletable('/mnt/user-data/outputs/query_result/abc.csv')).toBe(false)
    expect(isWorkspaceFileDeletable('\\outputs\\query_result\\abc.csv')).toBe(false)
    expect(isWorkspaceFileDeletable('/mnt/user-data/uploads/明细.xlsx')).toBe(true)
  })

  it('空路径与缺省值一律不可删除（拿不到真实路径时不给入口）', () => {
    expect(isWorkspaceFileDeletable('')).toBe(false)
    expect(isWorkspaceFileDeletable('   ')).toBe(false)
    expect(isWorkspaceFileDeletable(null)).toBe(false)
    expect(isWorkspaceFileDeletable(undefined)).toBe(false)
  })
})

describe('syncTruncatedTitle（悬浮显示被截断的完整文件名）', () => {
  // jsdom 不做真实排版，scrollWidth/clientWidth 恒为 0，因此按用例需要的测量值直接钉在实例上
  const makeLabel = (text: string, scrollWidth: number, clientWidth: number) => {
    const el = document.createElement('span')
    el.textContent = text
    Object.defineProperty(el, 'scrollWidth', { value: scrollWidth, configurable: true })
    Object.defineProperty(el, 'clientWidth', { value: clientWidth, configurable: true })
    return el
  }
  const hover = (el: HTMLElement | null) => syncTruncatedTitle({ currentTarget: el } as unknown as Event)

  it('文字被截断时挂上完整文件名', () => {
    const el = makeLabel('智能体与个人代理人发展对支付清算体系的影响研究及网联应对建议_提纲.docx', 463, 222)
    hover(el)
    expect(el.getAttribute('title')).toBe('智能体与个人代理人发展对支付清算体系的影响研究及网联应对建议_提纲.docx')
  })

  it('文字没被截断时不挂 title（短名字不弹冗余提示）', () => {
    const el = makeLabel('测试.sql', 42, 57)
    hover(el)
    expect(el.hasAttribute('title')).toBe(false)
  })

  it('宽度刚好相等算作未截断（scrollWidth 不严格大于 clientWidth）', () => {
    const el = makeLabel('2025年11月经营月报.docx', 152, 152)
    hover(el)
    expect(el.hasAttribute('title')).toBe(false)
  })

  it('未截断时清掉上一次残留的 title（面板拉宽后不留过期提示）', () => {
    const el = makeLabel('2025年11月经营月报.docx', 300, 150)
    hover(el)
    expect(el.getAttribute('title')).toBe('2025年11月经营月报.docx')

    // 面板被拉宽，同一行不再截断，再次悬浮应撤下提示
    Object.defineProperty(el, 'scrollWidth', { value: 150, configurable: true })
    Object.defineProperty(el, 'clientWidth', { value: 150, configurable: true })
    hover(el)
    expect(el.hasAttribute('title')).toBe(false)
  })

  it('空文字不挂 title（避免出现空白提示气泡）', () => {
    const el = makeLabel('   ', 100, 10)
    hover(el)
    expect(el.hasAttribute('title')).toBe(false)
  })

  it('拿不到 currentTarget 时静默返回，不抛错', () => {
    expect(() => hover(null)).not.toThrow()
  })
})

describe('formatFileSize（行尾文件大小）', () => {
  it('不足 1 KB 用整数字节', () => {
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(1023)).toBe('1023 B')
  })

  it('按 1024 进位到 KB / MB / GB', () => {
    expect(formatFileSize(1024)).toBe('1 KB')
    expect(formatFileSize(1536)).toBe('1.5 KB')
    expect(formatFileSize(1024 * 1024)).toBe('1 MB')
    expect(formatFileSize(12.3 * 1024 * 1024)).toBe('12.3 MB')
    expect(formatFileSize(1024 ** 3)).toBe('1 GB')
  })

  it('整数倍省掉 .0（1.0 MB → 1 MB）', () => {
    expect(formatFileSize(2 * 1024 ** 2)).toBe('2 MB')
    expect(formatFileSize(2048)).toBe('2 KB')
  })

  it('≥100 时取整，避免数字列宽度抖动', () => {
    expect(formatFileSize(1023 * 1024)).toBe('1023 KB')
    expect(formatFileSize(100 * 1024 ** 2)).toBe('100 MB')
  })

  it('恰好进位边界不提前跳单位', () => {
    expect(formatFileSize(1024 * 1024 - 1)).toBe('1024 KB')
  })

  it('负数、NaN、空值返回空串（拿不到大小时不显示 0 B）', () => {
    expect(formatFileSize(-1)).toBe('')
    expect(formatFileSize(Number.NaN)).toBe('')
    expect(formatFileSize(Number.POSITIVE_INFINITY)).toBe('')
    expect(formatFileSize(null)).toBe('')
    expect(formatFileSize(undefined)).toBe('')
  })
})

describe('isWorkspaceDirectoryArchiveDownloadable（目录打包下载白名单）', () => {
  it('只放开 outputs（用户拍板 2026-09-19）', () => {
    expect(isWorkspaceDirectoryArchiveDownloadable('outputs')).toBe(true)
    expect(isWorkspaceDirectoryArchiveDownloadable('workspace')).toBe(false)
    expect(isWorkspaceDirectoryArchiveDownloadable('uploads')).toBe(false)
  })

  it('outputs 下的子目录也可打包（追加放开 2026-09-24，顶层范围不变）', () => {
    expect(isWorkspaceDirectoryArchiveDownloadable('outputs/月报')).toBe(true)
    expect(isWorkspaceDirectoryArchiveDownloadable('outputs/月报/2026')).toBe(true)
    // 顶层范围没有跟着放开：其它顶层的子目录仍不可打包
    expect(isWorkspaceDirectoryArchiveDownloadable('workspace/steps')).toBe(false)
    expect(isWorkspaceDirectoryArchiveDownloadable('uploads/batch')).toBe(false)
    // 系统结果目录：包里本就不会有文件，给了入口只会得到一个空包报错
    expect(isWorkspaceDirectoryArchiveDownloadable('outputs/query_result')).toBe(false)
    expect(isWorkspaceDirectoryArchiveDownloadable('outputs/.tool-offload')).toBe(false)
    // 带沙箱前缀 / Windows 分隔符归一到同一口径
    expect(isWorkspaceDirectoryArchiveDownloadable('/mnt/user-data/outputs/月报')).toBe(true)
    expect(isWorkspaceDirectoryArchiveDownloadable('\\outputs\\月报')).toBe(true)
  })

  it('空值与未知目录保守返回 false', () => {
    expect(isWorkspaceDirectoryArchiveDownloadable('')).toBe(false)
    expect(isWorkspaceDirectoryArchiveDownloadable(null)).toBe(false)
    expect(isWorkspaceDirectoryArchiveDownloadable(undefined)).toBe(false)
    expect(isWorkspaceDirectoryArchiveDownloadable('outputs/query_result')).toBe(false)
  })
})
