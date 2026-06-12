<template>
  <div class="home">
    <el-card class="welcome-card">
      <div class="welcome-content">
        <h1>欢迎使用Vue聊天对话框示例</h1>
        <p class="subtitle">这是一个演示右下角聊天对话框的示例项目</p>

        <div class="feature-list">
          <el-row :gutter="20">
            <el-col :xs="24" :sm="12" :md="8" v-for="(feature, index) in features" :key="index">
              <el-card shadow="hover" class="feature-card">
                <div class="feature-icon">
                  <i :class="feature.icon"></i>
                </div>
                <h3>{{ feature.title }}</h3>
                <p>{{ feature.description }}</p>
              </el-card>
            </el-col>
          </el-row>
        </div>

        <div class="action-section">
          <h2>开始体验</h2>
          <p>点击右下角的聊天按钮，或前往页面A查看聊天对话框效果</p>

          <div class="action-buttons">
            <el-button
              type="primary"
              size="large"
              @click="goToPageA"
              icon="el-icon-right"
            >
              前往页面A
            </el-button>

            <el-button
              type="success"
              size="large"
              @click="openChat"
              icon="el-icon-chat-dot-round"
            >
              打开聊天
            </el-button>
          </div>
        </div>

        <div class="instruction">
          <h3>使用说明</h3>
          <el-steps :active="3" align-center>
            <el-step title="点击按钮" description="点击右下角聊天按钮" />
            <el-step title="开始聊天" description="输入消息并发送" />
            <el-step title="自由拖动" description="对话框不会遮挡内容" />
          </el-steps>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script>
export default {
  name: 'Home',
  data() {
    return {
      features: [
        {
          icon: 'el-icon-position',
          title: '右下角固定',
          description: '聊天按钮固定在页面右下角，随时可访问'
        },
        {
          icon: 'el-icon-chat-line-round',
          title: '实时聊天',
          description: '支持发送和接收消息，模拟真实聊天场景'
        },
        {
          icon: 'el-icon-view',
          title: '不遮挡内容',
          description: '抽屉式设计，不会遮挡页面主要内容'
        },
        {
          icon: 'el-icon-mobile',
          title: '响应式设计',
          description: '适配各种屏幕尺寸，移动端友好'
        },
        {
          icon: 'el-icon-magic-stick',
          title: '美观UI',
          description: '基于Element UI，界面美观大方'
        },
        {
          icon: 'el-icon-setting',
          title: '可配置',
          description: '支持自定义样式和功能扩展'
        }
      ]
    }
  },
  methods: {
    goToPageA() {
      this.$router.push('/page-a')
    },
    openChat() {
      // 这里需要获取聊天组件的引用
      // 在实际项目中，你可能需要使用Event Bus或Vuex来触发聊天对话框
      this.$message.success('请点击右下角的聊天按钮打开对话框')
    }
  }
}
</script>

<style scoped>
.home {
  min-height: calc(100vh - 120px);
}

.welcome-card {
  margin-bottom: 20px;
}

.welcome-content {
  text-align: center;
  padding: 40px 20px;
}

.welcome-content h1 {
  font-size: 2.5rem;
  color: #303133;
  margin-bottom: 10px;
  font-weight: 600;
}

.subtitle {
  font-size: 1.2rem;
  color: #606266;
  margin-bottom: 40px;
}

.feature-list {
  margin: 40px 0;
}

.feature-card {
  height: 200px;
  margin-bottom: 20px;
  text-align: center;
  transition: transform 0.3s;
  cursor: pointer;
}

.feature-card:hover {
  transform: translateY(-5px);
}

.feature-icon {
  font-size: 36px;
  color: #409eff;
  margin-bottom: 15px;
}

.feature-card h3 {
  color: #303133;
  margin-bottom: 10px;
  font-size: 18px;
}

.feature-card p {
  color: #606266;
  font-size: 14px;
  line-height: 1.5;
}

.action-section {
  margin: 50px 0;
  padding: 30px;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  border-radius: 12px;
}

.action-section h2 {
  color: #303133;
  margin-bottom: 10px;
}

.action-section p {
  color: #606266;
  margin-bottom: 20px;
}

.action-buttons {
  display: flex;
  justify-content: center;
  gap: 20px;
  flex-wrap: wrap;
}

.instruction {
  margin-top: 40px;
  padding: 30px;
  background-color: white;
  border-radius: 12px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.instruction h3 {
  text-align: center;
  margin-bottom: 30px;
  color: #303133;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .welcome-content {
    padding: 20px 10px;
  }

  .welcome-content h1 {
    font-size: 1.8rem;
  }

  .subtitle {
    font-size: 1rem;
  }

  .action-section {
    padding: 20px;
  }

  .action-buttons {
    flex-direction: column;
    align-items: center;
  }

  .feature-card {
    height: auto;
    min-height: 180px;
  }
}
</style>