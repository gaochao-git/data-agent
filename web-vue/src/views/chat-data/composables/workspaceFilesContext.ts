import type { InjectionKey } from 'vue'

/**
 * 文件管理/文件预览 tab 的共享作用域：面板持有状态，子组件 inject 后按原变量名渲染，
 * 避免用几十个 props 把同一份状态再转一手（本项目暂不做更细的 composable 拆分）。
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- 粗粒度共享作用域：键由面板统一提供，子组件按变量名消费
export const WorkspaceFilesContextKey: InjectionKey<Record<string, any>> = Symbol('workspace-files-context')
