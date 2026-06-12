<template>
  <div class="page-a">
    <!-- 页面内容 -->
    <div class="page-content">
      <el-card class="box-card">
        <div slot="header" class="clearfix">
          <h2>页面A - 主要内容区域</h2>
        </div>
        <div class="content-body">
          <el-button type="primary" @click="showDialog">
            打开提示对话框
          </el-button>

          <div class="content-section">
            <h3>页面内容示例</h3>
            <p>这里是页面的主要内容区域，右下角的聊天对话框不会遮挡这里的内容。</p>

            <el-row :gutter="20" class="demo-row">
              <el-col :span="8" v-for="i in 6" :key="i">
                <el-card shadow="hover" class="demo-card">
                  <div class="demo-card-content">
                    <h4>卡片 {{ i }}</h4>
                    <p>示例内容 {{ i }}</p>
                  </div>
                </el-card>
              </el-col>
            </el-row>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 聊天对话框组件 -->
    <chat-dialog ref="chatDialog" @opened="handleChatOpened" />

    <!-- 演示对话框 -->
    <el-dialog
      title="提示"
      v-model:visible="dialogVisible"
      width="30%"
    >
      <span>点击页面右下角的聊天按钮可以与客服进行在线沟通。</span>
      <span slot="footer" class="dialog-footer">
        <el-button @click="dialogVisible = false">知道了</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import ChatDialog from '@/components/ChatDialog/ChatDialog.vue'

export default {
  name: 'PageA',
  components: {
    ChatDialog
  },
  data() {
    return {
      dialogVisible: false
    }
  },
  methods: {
    showDialog() {
      this.dialogVisible = true
    },

    handleChatOpened() {
      console.log('聊天对话框已打开')
    }
  },
  mounted() {
    // 可以在这里添加自动打开聊天的逻辑
    // setTimeout(() => {
    //   this.$refs.chatDialog.openChatDialog()
    // }, 2000)
  }
}
</script>

<style scoped>
.page-a {
  min-height: 100vh;
  background-color: #f5f7fa;
  padding: 20px;
}

.page-content {
  max-width: 1200px;
  margin: 0 auto;
}

.box-card {
  margin-bottom: 20px;
}

.content-body {
  padding: 20px 0;
}

.content-section {
  margin-top: 30px;
  padding: 20px;
  background-color: white;
  border-radius: 8px;
}

.demo-row {
  margin-top: 20px;
}

.demo-card {
  margin-bottom: 20px;
}

.demo-card-content {
  min-height: 100px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}

/* 页面内容确保不会被聊天对话框遮挡 */
.page-content {
  position: relative;
  z-index: 1;
}
</style>