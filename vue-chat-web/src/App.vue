<template>
  <div id="app">
    <!-- 导航栏 -->
    <el-header class="app-header">
      <div class="header-content">
        <div class="logo">
          <img src="./assets/logo.png" alt="Logo" class="logo-img" />
          <span class="app-title">Vue聊天对话框示例</span>
        </div>
        <el-menu
          :default-active="activeMenu"
          class="nav-menu"
          mode="horizontal"
          router
          background-color="#409eff"
          text-color="#fff"
          active-text-color="#ffd04b"
        >
          <el-menu-item index="/">
            <i class="el-icon-house"></i>
            首页
          </el-menu-item>
          <el-menu-item index="/page-a">
            <i class="el-icon-document"></i>
            页面A
          </el-menu-item>
        </el-menu>
      </div>
    </el-header>

    <!-- 主要内容区域 -->
    <el-main class="app-main">
      <router-view />
    </el-main>

    <!-- 全局聊天对话框 -->
    <chat-dialog v-if="showGlobalChat" />
  </div>
</template>

<script>
import ChatDialog from '@/components/ChatDialog/ChatDialog.vue'

export default {
  name: 'App',
  components: {
    ChatDialog
  },
  data() {
    return {
      showGlobalChat: true,
      activeMenu: '/'
    }
  },
  watch: {
    $route(to) {
      this.activeMenu = to.path
    }
  },
  mounted() {
    this.activeMenu = this.$route.path
  }
}
</script>

<style scoped>
#app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-header {
  background: linear-gradient(135deg, #409eff 0%, #337ecc 100%);
  height: 60px;
  line-height: 60px;
  padding: 0 20px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
  position: sticky;
  top: 0;
  z-index: 1000;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-img {
  height: 36px;
  width: 36px;
}

.app-title {
  color: white;
  font-size: 18px;
  font-weight: bold;
}

.nav-menu {
  border: none;
  height: 60px;
}

.nav-menu .el-menu-item {
  height: 60px;
  line-height: 60px;
  font-size: 14px;
  transition: all 0.3s;
}

.nav-menu .el-menu-item:hover {
  background-color: rgba(255, 255, 255, 0.1) !important;
}

.nav-menu .el-menu-item.is-active {
  border-bottom: 3px solid #ffd04b;
}

.app-main {
  flex: 1;
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
  width: 100%;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .app-header {
    height: auto;
    padding: 10px;
  }

  .header-content {
    flex-direction: column;
    gap: 10px;
  }

  .nav-menu {
    width: 100%;
  }

  .app-main {
    padding: 10px;
  }
}
</style>