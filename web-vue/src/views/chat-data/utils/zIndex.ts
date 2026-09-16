/**
 * EP 组件 :z-index prop 用的数字常量（prop 只接受 number，用不了 CSS 变量）。
 *
 * 与 ../styles/z-index.css 中的同名 CSS 变量一一对应，改值必须两处同步。
 * 仅收录 EP 弹层 prop 实际引用的值；纯 CSS 场景直接用 var(--z-*)。
 * 组件内部相对排序的小层级（1~320）不在此管理。
 */
export const Z_INDEX = {
  /** 查询结果全屏 dialog（≈ var(--z-dialog-fullscreen)） */
  DIALOG_FULLSCREEN: 3900,
  /** 普通 dialog，如保存到看板（≈ var(--z-dialog)） */
  DIALOG: 4000,
  /** 评测 drawer（≈ var(--z-drawer)） */
  DRAWER: 4200,
  /** drawer 内二级 dialog（≈ var(--z-dialog-nested)） */
  DIALOG_NESTED: 4300,
} as const
