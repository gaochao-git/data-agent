import type { RouteRecordRaw } from 'vue-router'
import InspectionView from './views/index.vue'

const inspectionRoutes: RouteRecordRaw[] = [
  {
    path: '/inspection',
    name: 'Inspection',
    component: InspectionView,
    meta: {
      title: '执法检查',
      requiresAuth: true
    }
  },
  {
    path: '/inspection/:agentId',
    name: 'InspectionWithAgent',
    component: InspectionView,
    meta: {
      title: '执法检查',
      requiresAuth: true
    }
  }
]

export default inspectionRoutes
