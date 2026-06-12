// src/router/index.js
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('@/views/Home.vue')
  },
  {
    path: '/page-a',
    name: 'PageA',
    component: () => import('@/views/PageA.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router