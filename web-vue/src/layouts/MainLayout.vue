<script setup lang="ts">
/**
 * 主布局组件 - 参考桌面智能问数项目
 */
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

// 侧边栏折叠状态
const sidebarCollapsed = ref(false)

// 顶部一级导航菜单
const topMenuItems = [
  { key: 'chatdata', label: '智能问数', icon: 'fa-chart-simple', path: '/chatdata' },
]

// 侧边栏菜单
const sidebarMenu = [
  { key: 'chatdata', label: '智能问数', icon: 'fa-chart-simple', path: '/chatdata' },
]

// 兼容旧路由
const menuItems = topMenuItems

// 当前激活的顶部菜单
const activeTopMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/chatdata')) return 'chatdata'
  return 'chatdata'
})

// 当前模块信息
const currentModule = computed(() => {
  return { label: '智能问数', icon: 'fa-chart-simple' }
})

// 当前侧边栏菜单
const currentSidebarMenu = computed(() => {
  return sidebarMenu
})

// 当前激活的侧边栏菜单项
const activeSidebarItem = computed(() => {
  const path = route.path
  if (path.startsWith('/chatdata')) return 'chatdata'
  for (const item of currentSidebarMenu.value) {
    if (path === item.path || path.startsWith(item.path + '/')) return item.key
  }
  return 'chatdata'
})

// 兼容旧代码
const activeMenu = activeTopMenu

// 切换侧边栏
const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

// 导航 - 接受菜单key作为参数
const navigateTo = (menuKey: string) => {
  const menuItem = topMenuItems.find(item => item.key === menuKey) || sidebarMenu.find(item => item.key === menuKey)
  if (menuItem) {
    router.push({ path: menuItem.path })
    return
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
            <i class="fa-solid fa-chart-simple"></i>
          </div>
          <span class="logo-text">Data Agent</span>
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

        <!-- 导航菜单 -->
        <nav class="sidebar-nav">
          <template v-for="item in currentSidebarMenu" :key="item.key">
            <div
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

/* 内容区 */
.main-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
</style>
