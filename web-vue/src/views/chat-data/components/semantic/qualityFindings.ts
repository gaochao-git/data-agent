import { h, type FunctionalComponent } from 'vue'
import { ElMessageBox } from 'element-plus'
import type { SemanticQualityFinding } from '../../types'

// 严重度→文字色（error 红 / warning 琥珀 / info 灰），与项目严重度口径一致
const severityClass = (severity: string) =>
  severity === 'error' ? 'text-red-500' : severity === 'warning' ? 'text-amber-500' : 'text-gray-500'

interface QualityFindingsListProps {
  findings: SemanticQualityFinding[]
  title?: string
}

/**
 * 质量提示列表：保存前校验弹窗与导入预览弹窗共用的同一呈现，
 * 两处不得各写各的（曾经漂移出两套颜色与两套排版）。
 * 函数式组件：OssieImportDialog 在模板中直接使用，保存弹窗经 h() 渲染。
 */
export const QualityFindingsList: FunctionalComponent<QualityFindingsListProps> = (props, { attrs }) =>
  h('div', { class: ['rounded border border-gray-100 bg-gray-50/60 px-2 py-1.5', attrs.class as string] }, [
    h('div', { class: 'text-[11px] font-medium text-gray-400' }, props.title || '触发的质量规则'),
    // chat-data 不引 preflight：必须显式去掉 ul 的默认圆点与缩进，否则冒出浏览器样式；
    // 一条提示一行不换行，超长时卡片内横向滚动
    h('ul', { class: 'mt-1 list-none m-0 p-0 space-y-0.5 overflow-x-auto' }, props.findings.map(f =>
      h('li', { key: f.code, class: `text-[11px] leading-4 whitespace-nowrap ${severityClass(f.severity)}` }, `[${f.code}] ${f.message}`)
    )),
  ])
QualityFindingsList.props = ['findings', 'title']

/**
 * 保存前质量提示确认弹窗：与导入「先校验再确认」同一交互语义。
 * 返回 true=仍要保存；false=去修改。
 */
export async function confirmSaveQualityFindings(findings: SemanticQualityFinding[]): Promise<boolean> {
  try {
    await ElMessageBox.confirm(
      // 与导入预览同构：正常字号的引导行 + 默认「触发的质量规则」卡片（不要把关键信息塞进 11px 小标题位）
      h('div', null, [
        h('div', { class: 'text-[13px] leading-5 text-gray-700' }, `发现 ${findings.length} 条质量提示（不影响保存）：`),
        h(QualityFindingsList, { findings, class: 'mt-2' }),
      ]),
      '保存前校验',
      {
        type: 'warning',
        customClass: 'chatdata-message-box chatdata-message-box-wide',
        modalClass: 'chatdata-message-box-overlay',
        confirmButtonText: '仍要保存',
        cancelButtonText: '去修改',
      }
    )
    return true
  } catch {
    return false
  }
}
