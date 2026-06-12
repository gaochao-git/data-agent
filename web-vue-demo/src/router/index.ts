import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: MainLayout,
      redirect: '/chatdata',
      children: [
        {
          path: 'chatdata',
          name: 'ChatData',
          component: () => import('@/views/chat-data/views/index.vue'),
          meta: { title: '智能问数' }
        },
        {
          path: 'assistant',
          name: 'Assistant',
          component: () => import('@/views/ComingSoon.vue'),
          meta: { title: '智能助手' }
        },
        {
          path: 'inspection',
          name: 'Inspection',
          component: () => import('@/views/inspection/views/index.vue'),
          meta: { title: '执法检查' }
        },
        {
          path: 'complaint',
          name: 'Complaint',
          component: () => import('@/views/ComingSoon.vue'),
          meta: { title: '投诉分析' }
        },
        {
          path: 'test-dialog',
          name: 'TestDialog',
          component: () => import('@/views/TestChatDataDialog.vue'),
          meta: { title: '弹窗测试' }
        },
        {
          path: 'expert',
          name: 'Expert',
          component: () => import('@/views/expert/index.vue'),
          meta: { title: '法律助手' }
        },
        {
          path: 'expert/:conversationId',
          name: 'ExpertConversation',
          component: () => import('@/views/expert/index.vue'),
          meta: { title: '法律助手' }
        },
      ]
    },
  ],
})

export default router
