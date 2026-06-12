<script setup lang="ts">
/**
 * 主布局组件 - 参考桌面智能问数项目
 */
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()

// 已开发的路由路径
const availableRoutes = ['/chatdata', '/inspection', '/expert']

// 侧边栏折叠状态
const sidebarCollapsed = ref(false)

// 展开的菜单
const expandedMenu = ref<string[]>([])

// 顶部一级导航菜单 - 执法检查子功能模块
const topMenuItems = [
  { key: 'home', label: '法律助手', icon: 'fa-scale-balanced', path: '/expert' },
  { key: 'fact_analysis', label: '事实认定分析', icon: 'fa-file-lines', path: '/inspection' },
  { key: 'inspection_report', label: '检查报告撰写', icon: 'fa-pen-to-square', path: '/inspection' },
  { key: 'rectification_notice', label: '整改通知书撰写', icon: 'fa-envelope-open-text', path: '/inspection' },
  { key: 'info_form', label: '信息简表撰写', icon: 'fa-table', path: '/inspection' },
  { key: 'evidence_catalog', label: '证据目录撰写', icon: 'fa-list-check', path: '/inspection' },
  { key: 'stats', label: '统计分析', icon: 'fa-chart-simple', path: '/chatdata' },
]

// 侧边栏二级菜单 - 执法检查子功能菜单
const sidebarMenu = [
  { key: 'fact_analysis', label: '事实认定分析', icon: 'fa-file-lines', path: '/inspection' },
  { key: 'inspection_report', label: '检查报告撰写', icon: 'fa-pen-to-square', path: '/inspection' },
  { key: 'rectification_notice', label: '整改通知书撰写', icon: 'fa-envelope-open-text', path: '/inspection' },
  { key: 'info_form', label: '信息简表撰写', icon: 'fa-table', path: '/inspection' },
  { key: 'evidence_catalog', label: '证据目录撰写', icon: 'fa-list-check', path: '/inspection' },
  { key: 'stats', label: '统计分析', icon: 'fa-chart-simple', path: '/chatdata' },
]

// 兼容旧路由
const menuItems = topMenuItems

// 当前激活的顶部菜单
const activeTopMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/chatdata')) return 'stats'
  if (path.startsWith('/expert')) return 'home'
  if (path.startsWith('/inspection')) {
    // 根据查询参数判断具体是哪个子功能
    const agentId = route.query.agentId as string
    if (agentId?.includes('fact') || agentId?.includes('ssrd')) return 'fact_analysis'
    if (agentId?.includes('rectification') || agentId?.includes('zgtzs')) return 'rectification_notice'
    if (agentId?.includes('info') || agentId?.includes('xxjb')) return 'info_form'
    if (agentId?.includes('evidence') || agentId?.includes('zjml')) return 'evidence_catalog'
    return 'inspection_report' // 默认检查报告
  }
  return 'home'
})

// 当前模块信息（根据路由显示执法检查或子功能）
const currentModule = computed(() => {
  const path = route.path
  if (path.startsWith('/inspection')) {
    return { label: '执法检查', icon: 'fa-magnifying-glass' }
  }
  return { label: '执法检查', icon: 'fa-magnifying-glass' }
})

// 当前侧边栏菜单
const currentSidebarMenu = computed(() => {
  // 如果当前在执法检查路径下，返回执法检查子功能菜单
  if (route.path.startsWith('/inspection')) {
    return sidebarMenu.filter(item => item.key !== 'stats') // 移除统计，只保留执法检查相关
  }
  return sidebarMenu.filter(item => item.key !== 'stats') // 默认显示执法检查菜单
})

// 当前激活的侧边栏菜单项
const activeSidebarItem = computed(() => {
  const path = route.path
  // 检查路径匹配
  if (path.startsWith('/chatdata')) return 'stats'
  if (path.startsWith('/inspection')) {
    // 根据查询参数判断具体是哪个子功能
    const agentId = route.query.agentId as string
    if (agentId?.includes('fact') || agentId?.includes('ssrd')) return 'fact_analysis'
    if (agentId?.includes('rectification') || agentId?.includes('zgtzs')) return 'rectification_notice'
    if (agentId?.includes('info') || agentId?.includes('xxjb')) return 'info_form'
    if (agentId?.includes('evidence') || agentId?.includes('zjml')) return 'evidence_catalog'
    return 'inspection_report' // 默认检查报告
  }
  for (const item of currentSidebarMenu.value) {
    if (path === item.path || path.startsWith(item.path + '/')) return item.key
  }
  return 'fact_analysis'
})

// 兼容旧代码
const activeMenu = activeTopMenu

// 切换侧边栏
const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

// 切换子菜单
const toggleSubmenu = (key: string) => {
  if (expandedMenu.value.includes(key)) {
    expandedMenu.value = expandedMenu.value.filter(k => k !== key)
  } else {
    expandedMenu.value = [...expandedMenu.value, key]
  }
}

// 顶部菜单到agentId的映射
const menuToAgentId: Record<string, string> = {
  'fact_analysis': 'fact_analysis',   // 事实认定分析
  'inspection_report': 'report_jcbg', // 检查报告撰写
  'rectification_notice': 'report_zgtzs',   // 整改通知书撰写
  'info_form': 'report_xxjb',               // 信息简表撰写
  'evidence_catalog': 'report_zjml',        // 证据目录撰写
}

// 导航 - 接受菜单key作为参数
const navigateTo = (menuKey: string) => {
  // 处理专家/法律助手页面
  if (menuKey === 'home') {
    router.push({ path: '/expert' })
    return
  }

  // 处理统计分析页面
  if (menuKey === 'stats') {
    router.push({ path: '/chatdata' })
    return
  }

  // 检查是否是执法检查相关菜单
  if (menuToAgentId[menuKey]) {
    router.push({
      path: '/inspection',
      query: { agentId: menuToAgentId[menuKey] }
    })
    return
  }

  // 其他情况查找菜单项的path
  const menuItem = topMenuItems.find(item => item.key === menuKey)
  if (menuItem && availableRoutes.some(r => menuItem.path.startsWith(r))) {
    router.push({ path: menuItem.path })
  } else {
    ElMessage.info('功能开发中，敬请期待')
  }
}
</script>

<template>
  <div class="main-layout">
    <!-- 顶部 Header -->
    <header class="main-header">
      <div class="header-left">
        <button class="toggle-btn" @click="toggleSidebar">
          <i class="fa-solid fa-bars"></i>
        </button>
        <div class="logo">
          <div class="logo-icon">
            <i class="fa-solid fa-file-lines"></i>
          </div>
          <span class="logo-text">审批监管管理系统</span>
        </div>
      </div>

      <!-- 顶部导航 -->
      <nav class="header-nav">
        <button
          v-for="item in topMenuItems"
          :key="item.key"
          class="header-nav-item"
          :class="{ active: activeTopMenu === item.key }"
          @click="navigateTo(item.key)"
        >
          <i :class="['fa-solid', item.icon]"></i>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="header-right">
        <button class="notification-btn">
          <i class="fa-solid fa-bell"></i>
          <span class="notification-dot"></span>
        </button>

        <el-dropdown>
          <div class="user-info">
            <i class="fa-solid fa-user"></i>
            <span class="user-name">管理员</span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item>个人设置</el-dropdown-item>
              <el-dropdown-item divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <!-- 主体区域 -->
    <div class="main-body">
      <!-- 侧边导航 -->
      <aside class="main-sidebar" :class="{ collapsed: sidebarCollapsed }">
        <!-- 模块卡片 -->
        <div v-if="!sidebarCollapsed" class="sidebar-module-card">
          <div class="module-icon">
            <i :class="['fa-solid', currentModule.icon]"></i>
          </div>
          <div class="module-info">
            <div class="module-name">{{ currentModule.label }}</div>
            <div class="module-status">
              <span class="status-dot"></span>
              <span>实时分析中</span>
            </div>
          </div>
          <i class="fa-solid fa-chevron-right module-arrow"></i>
        </div>

        <!-- 导航菜单 - 二级菜单 -->
        <nav class="sidebar-nav">
          <template v-for="item in currentSidebarMenu" :key="item.key">
            <!-- 有子菜单的项 -->
            <div v-if="item.hasSubmenu">
              <div
                class="nav-item"
                :class="{ expanded: expandedMenu.includes(item.key) }"
                @click="toggleSubmenu(item.key)"
              >
                <i :class="['fa-solid', item.icon, 'nav-icon']"></i>
                <span v-if="!sidebarCollapsed" class="nav-label">{{ item.label }}</span>
                <i v-if="!sidebarCollapsed" :class="['fa-solid', 'fa-chevron-down', 'nav-arrow', { rotated: expandedMenu.includes(item.key) }]"></i>
              </div>
              <!-- 子菜单 -->
              <div v-if="expandedMenu.includes(item.key) && !sidebarCollapsed" class="submenu">
                <div
                  v-for="sub in item.submenu"
                  :key="sub.key"
                  class="submenu-item"
                  :class="{ active: activeSidebarItem === sub.key }"
                  @click.stop="navigateTo(sub.key)"
                >
                  <span class="submenu-dot"></span>
                  <span>{{ sub.label }}</span>
                </div>
              </div>
            </div>
            <!-- 普通菜单项 -->
            <div
              v-else
              class="nav-item"
              :class="{ active: activeSidebarItem === item.key }"
              @click="navigateTo(item.key)"
            >
              <i :class="['fa-solid', item.icon, 'nav-icon']"></i>
              <span v-if="!sidebarCollapsed" class="nav-label">{{ item.label }}</span>
            </div>
          </template>
        </nav>
      </aside>

      <!-- 内容区域 -->
      <main class="main-content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.main-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  overflow: hidden;
}

/* Header - 参考桌面项目 */
.main-header {
  height: 64px;
  background: linear-gradient(to right, #0f4c81, #0a3c68);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  position: relative;
  z-index: auto;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 32px;
}

.toggle-btn {
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.8);
  transition: all 0.2s;
}

.toggle-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-icon {
  width: 40px;
  height: 40px;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(8px);
}

.logo-icon i {
  font-size: 20px;
  color: #fff;
}

.logo-text {
  font-size: 20px;
  font-weight: 600;
  color: #fff;
}

/* 顶部导航 */
.header-nav {
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  color: rgba(255, 255, 255, 0.8);
  font-size: 14px;
  transition: all 0.2s;
}

.header-nav-item:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  transform: scale(1.02);
}

.header-nav-item.active {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
}

.header-nav-item i {
  font-size: 14px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 通知按钮 */
.notification-btn {
  position: relative;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.8);
  transition: all 0.2s;
}

.notification-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.notification-dot {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 8px;
  height: 8px;
  background: #ef4444;
  border-radius: 50%;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 8px 16px;
  border-radius: 8px;
  transition: all 0.2s;
  color: rgba(255, 255, 255, 0.8);
}

.user-info:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}

.user-info i {
  font-size: 16px;
}

.user-name {
  font-size: 14px;
  color: inherit;
}

/* 主体 - 参考桌面项目 */
.main-body {
  flex: 1;
  display: flex;
  gap: 24px;
  padding-right: 24px;
  overflow: hidden;
  min-height: 0;
  background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
}

/* 侧边栏 - 参考桌面项目（圆角卡片样式） */
.main-sidebar {
  width: 280px;
  margin: 24px;
  margin-right: 0;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  border: 1px solid #f1f5f9;
  transition: all 0.3s ease;
  flex-shrink: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.main-sidebar.collapsed {
  width: 64px;
  margin: 24px;
  margin-right: 0;
}

/* 模块卡片 */
.sidebar-module-card {
  margin: 16px;
  padding: 16px;
  background: linear-gradient(135deg, #e8f4ff 0%, #f0f7ff 100%);
  border-radius: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.sidebar-module-card:hover {
  box-shadow: 0 4px 12px rgba(31, 130, 217, 0.15);
}

.module-icon {
  width: 48px;
  height: 48px;
  background: linear-gradient(135deg, #1f82d9 0%, #135589 100%);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(31, 130, 217, 0.3);
  flex-shrink: 0;
}

.module-icon i {
  font-size: 20px;
  color: #fff;
}

.module-info {
  flex: 1;
  min-width: 0;
}

.module-name {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}

.module-status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}

.status-dot {
  width: 6px;
  height: 6px;
  background: #22c55e;
  border-radius: 50%;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.module-status span {
  font-size: 12px;
  color: #64748b;
}

.module-arrow {
  font-size: 12px;
  color: #94a3b8;
}

/* 导航菜单 */
.sidebar-nav {
  flex: 1;
  padding: 8px 12px;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  cursor: pointer;
  color: #475569;
  transition: all 0.2s;
  margin-bottom: 4px;
}

.nav-item:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.nav-item.active {
  background: linear-gradient(135deg, #1f82d9 0%, #135589 100%);
  color: #fff;
  box-shadow: 0 4px 12px rgba(31, 130, 217, 0.3);
}

.nav-icon {
  width: 20px;
  text-align: center;
  font-size: 16px;
}

.nav-label {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  flex: 1;
}

.nav-arrow {
  font-size: 10px;
  color: #94a3b8;
  transition: transform 0.2s;
}

.nav-arrow.rotated {
  transform: rotate(180deg);
}

.nav-item.expanded {
  background: #f1f5f9;
  color: #1e293b;
}

/* 子菜单 */
.submenu {
  margin-left: 20px;
  padding: 4px 0;
}

.submenu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-radius: 8px;
  cursor: pointer;
  color: #64748b;
  font-size: 13px;
  transition: all 0.2s;
}

.submenu-item:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.submenu-item.active {
  background: #eff6ff;
  color: #1f82d9;
  font-weight: 500;
}

.submenu-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
}

.submenu-item.active .submenu-dot {
  background: #1f82d9;
}

/* 内容区 */
.main-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
</style>
