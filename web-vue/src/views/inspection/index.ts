/**
 * 执法检查模块入口
 */

// 导出路由配置
export { default as inspectionRoutes } from './routes'

// 导出核心布局组件 (供 GeneralAssistant 嵌入使用)
export { default as EmbeddedInspectionLayout } from './EmbeddedLayout.vue'

// 导出主视图组件
export { default as InspectionView } from './views/index.vue'

// 导出类型
export * from './types'

// 导出 API
export * from './api/inspection'

// 导出 Composables
export * from './composables'

// 导出工具函数
export * from './utils'

// 导出常量
export * from './constants'
