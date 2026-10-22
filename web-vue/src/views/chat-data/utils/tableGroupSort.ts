/**
 * 表分组弹窗内的表列表排序（纯函数，便于单测）。
 *
 * 两种口径，由弹窗右上角按钮切换：
 * - `selected`：已勾选的表排在最前（组内按表名），用于确认「这个分组里都选了哪些表」；
 * - `name`：纯按表名（localeCompare），用于快速定位某张表。
 *
 * 两个口径都以表名为次级键，保证同一份数据多次渲染顺序稳定（不依赖 ds_tables 原始顺序）。
 */
export type TableGroupSortMode = 'selected' | 'name'

export function sortTableGroupTables<T extends { table_name: string }>(
  tables: T[],
  selectedTableNames: Set<string>,
  mode: TableGroupSortMode,
): T[] {
  const byName = (a: T, b: T) => a.table_name.localeCompare(b.table_name)
  if (mode === 'name') return [...tables].sort(byName)
  return [...tables].sort((a, b) => {
    const aSelected = selectedTableNames.has(a.table_name) ? 0 : 1
    const bSelected = selectedTableNames.has(b.table_name) ? 0 : 1
    return aSelected - bSelected || byName(a, b)
  })
}
