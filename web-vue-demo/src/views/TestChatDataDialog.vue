<script setup lang="ts">
/**
 * 临时测试页面：验证 ChatDataDialog / ChatDataFloat 组件
 */
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDataDialog, ChatDataFloat } from '@/views/chat-data'
import type { ChatDataModeLabels, ChatDataSqlChangePayload } from '@/views/chat-data'

const dialogVisible = ref(false)
const conversationDialogVisible = ref(false)
const dashboardDialogVisible = ref(false)
const floatRef = ref<InstanceType<typeof ChatDataFloat> | null>(null)

const defaultSqlRuleSql = `SELECT
  category_name,
  COUNT(1) AS order_cnt
FROM complaint_orders
GROUP BY category_name
ORDER BY order_cnt DESC
LIMIT 20;`

const savedSql = ref(defaultSqlRuleSql)
const savedBuilderConfig = ref<Record<string, unknown> | null>(null)
const sqlRuleModeLabels: ChatDataModeLabels = {
  conversation: '数据智能体',
  builder: '可视化',
  editor: 'SQL',
  dashboard: '数据看板',
}

const handleSqlRuleSave = (payload: ChatDataSqlChangePayload) => {
  if (payload.mode === 'builder') {
    // 可视化保存：同步覆盖 SQL + Builder 配置
    savedSql.value = payload.sql
    savedBuilderConfig.value = payload.builderConfig || null
  } else {
    // SQL 编辑保存：仅保留 SQL，清空 Builder 配置，避免错配
    savedSql.value = payload.sql
    savedBuilderConfig.value = null
  }
  console.log('[SQL规则] 保存载荷:', payload)
  ElMessage.success('SQL规则已回传父组件')
}
</script>

<template>
  <div class="flex items-center justify-center h-full">
    <div class="text-center space-y-8">
      <h2 class="text-xl font-medium text-gray-700">智能问数组件测试</h2>

      <div class="space-y-4">
        <p class="text-sm text-gray-400 font-medium">两种嵌入方式</p>
        <div class="flex gap-4 justify-center">
          <!-- 弹窗模式 -->
          <el-button class="mode-btn mode-btn-primary" @click="dialogVisible = true">
            <i class="fa-solid fa-expand mode-btn-icon"></i>
            弹窗模式
          </el-button>

          <!-- 悬浮窗模式 -->
          <el-button class="mode-btn mode-btn-success" @click="floatRef?.open()">
            <i class="fa-solid fa-up-right-from-square mode-btn-icon"></i>
            SQL规则
          </el-button>
        </div>

        <div class="flex gap-4 justify-center">
          <el-button class="mode-btn mode-btn-conversation" @click="conversationDialogVisible = true">
            <i class="fa-solid fa-comments mode-btn-icon"></i>
            conversation
          </el-button>
          <el-button class="mode-btn mode-btn-dashboard" @click="dashboardDialogVisible = true">
            <i class="fa-solid fa-chart-pie mode-btn-icon"></i>
            dashboard
          </el-button>
        </div>
      </div>
    </div>

    <!-- 弹窗模式 -->
    <ChatDataDialog
      v-model:visible="dialogVisible"
      :options="{
        parentComponentName: '投诉举报',
        defaultMode: 'builder',
        hideTabs: ['dashboard']
      }"
    />
    <ChatDataDialog
      v-model:visible="conversationDialogVisible"
      :options="{
        parentComponentName: '投诉举报',
        defaultMode: 'conversation',
        hideTabs: ['editor']
      }"
    />
    <ChatDataDialog
      v-model:visible="dashboardDialogVisible"
      :options="{
        parentComponentName: '投诉举报',
        defaultMode: 'dashboard',
        defaultDashboardName: '某电商数据分析大屏111',
        hideTabs: ['conversation']
      }"
    />

    <!-- 悬浮窗模式 -->
    <ChatDataFloat
      ref="floatRef"
      :show-fab="false"
      :fullscreen="true"
      :initial-width="1400"
      :initial-height="820"
      :options="{
        parentComponentName: '执法检查',
        defaultMode: 'editor',
        initialSql: savedSql,
        initialBuilderConfig: savedBuilderConfig || undefined,
        onSqlChange: handleSqlRuleSave,
        modeLabels: sqlRuleModeLabels,
        hideBuilderChartPanel: true,
        hideTabs: ['dashboard']
      }"
    />
  </div>
</template>

<style scoped>
.mode-btn {
  min-width: 186px;
  height: 54px;
  border-radius: 10px;
  border: none;
  font-size: 18px;
  font-weight: 600;
}

.mode-btn-icon {
  margin-right: 8px;
}

.mode-btn-primary {
  background: #409eff;
  color: #fff;
}

.mode-btn-primary:hover {
  background: #66b1ff;
  color: #fff;
}

.mode-btn-success {
  background: #67c23a;
  color: #fff;
}

.mode-btn-success:hover {
  background: #85ce61;
  color: #fff;
}

.mode-btn-conversation {
  background: #909399;
  color: #fff;
}

.mode-btn-conversation:hover {
  background: #a6a9ad;
  color: #fff;
}

.mode-btn-dashboard {
  background: #e6a23c;
  color: #fff;
}

.mode-btn-dashboard:hover {
  background: #ebb563;
  color: #fff;
}
</style>
