/**
 * ChatData 模块入口
 */

// 导出路由配置
export const chatDataRoutes = [
  {
    path: '/chatdata',
    name: 'ChatData',
    component: () => import('./views/index.vue'),
    meta: {
      title: '智能问数',
    },
  },
]

// 导出类型
export * from './types'

// 导出 API
export * from './api'

// 导出 Composables
export * from './composables'

// 导出工具函数
export * from './utils'

// 导出弹窗组件（供外部模块直接使用）
export { default as ChatDataDialog } from './views/ChatDataDialog.vue'

// 导出悬浮窗组件（任意页面引入，可拖动、可缩放）
export { default as ChatDataFloat } from './views/ChatDataFloat.vue'
