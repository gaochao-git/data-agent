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
        }
      ]
    },
  ],
})

export default router
