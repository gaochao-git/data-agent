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
          component: () => import('@/views/chat-data/pages/index.vue'),
          meta: { title: '智能问数' }
        },
        {
          path: 'chatdata/ontology-graph-test',
          name: 'OntologyGraphTest',
          component: () => import('@/views/chat-data/pages/OntologyGraphTest.vue'),
          meta: { title: '本体图引擎验证' }
        },
        {
          path: 'test-dialog',
          name: 'TestDialog',
          component: () => import('@/views/TestChatDataDialog.vue'),
          meta: { title: '弹窗测试' }
        }
      ]
    },
  ],
})

export default router
