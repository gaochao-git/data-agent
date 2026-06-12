<script setup lang="ts">
/**
 * 保存到看板的弹窗组件
 * 将生成的 SQL 保存到分析大屏的面板中
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { listDashboards, addPanel, createDashboard, generatePanelName, type Dashboard } from '../api/dashboard'

interface Props {
  visible: boolean
  sql: string
  chartType?: string
  chartConfig?: Record<string, unknown>
  defaultName?: string
  sourceThreadId?: string
  datasourceId?: string
  hideSql?: boolean
  hideChartType?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  chartType: 'table',
  defaultName: '',
  hideSql: false,
  hideChartType: false
})

const emit = defineEmits<{
  'update:visible': [value: boolean]
  close: []
}>()

// 状态
const dashboards = ref<Dashboard[]>([])
const selectedDashboardId = ref('')
const chartType = ref(props.chartType)
const loading = ref(false)
const showNewDashboard = ref(false)
const newDashboardName = ref('')

// 图表类型选项
const CHART_TYPES = [
  { value: 'table', label: '表格', icon: 'fa-table' },
  { value: 'bar', label: '柱状图', icon: 'fa-chart-bar' },
  { value: 'line', label: '折线图', icon: 'fa-chart-line' },
  { value: 'pie', label: '饼图', icon: 'fa-chart-pie' },
  { value: 'kpi', label: '指标卡', icon: 'fa-hashtag' }
]

// 监听 visible 变化加载看板列表
watch(
  () => props.visible,
  async (visible) => {
    if (visible) {
      chartType.value = props.chartType
      await loadDashboards()
    }
  }
)

// 加载看板列表
const loadDashboards = async () => {
  try {
    const list = await listDashboards()
    dashboards.value = list
    const firstDashboard = list[0]
    if (firstDashboard && !selectedDashboardId.value) {
      selectedDashboardId.value = firstDashboard.id
    }
  } catch (err) {
    console.error('加载看板列表失败:', err)
  }
}

// 创建新看板
const handleCreateDashboard = async () => {
  if (!newDashboardName.value.trim()) {
    ElMessage.warning('请输入看板名称')
    return
  }

  loading.value = true
  try {
    const newDashboard = await createDashboard({
      name: newDashboardName.value.trim()
    })
    dashboards.value = [newDashboard, ...dashboards.value]
    selectedDashboardId.value = newDashboard.id
    newDashboardName.value = ''
    showNewDashboard.value = false
    ElMessage.success('看板创建成功')
  } catch (err) {
    ElMessage.error('创建看板失败')
    console.error(err)
  } finally {
    loading.value = false
  }
}

// 保存面板
const handleSave = async () => {
  if (!selectedDashboardId.value) {
    ElMessage.warning('请选择目标看板')
    return
  }
  loading.value = true
  try {
    const titleResult = await generatePanelName({
      name: props.defaultName,
      sql: props.sql,
      chart_type: chartType.value as 'table' | 'bar' | 'line' | 'pie' | 'kpi'
    })
    await addPanel(selectedDashboardId.value, {
      name: titleResult.name,
      sql: props.sql,
      chart_type: chartType.value as 'table' | 'bar' | 'line' | 'pie' | 'kpi',
      chart_config: props.chartConfig,
      source_thread_id: props.sourceThreadId,
      datasource_id: props.datasourceId
    })
    ElMessage.success('面板添加成功')
    handleClose()
  } catch (err) {
    ElMessage.error('添加面板失败')
    console.error(err)
  } finally {
    loading.value = false
  }
}

// 关闭弹窗
const handleClose = () => {
  emit('update:visible', false)
  emit('close')
}
</script>

<template>
  <el-dialog :model-value="visible" title="保存到看板" width="480px" append-to-body :z-index="4000" @close="handleClose">
    <!-- 选择看板 -->
    <div class="mb-4 last:mb-0">
      <label class="block text-sm font-medium text-gray-700 mb-2">目标看板</label>
      <div v-if="showNewDashboard" class="flex gap-2">
        <el-input
          v-model="newDashboardName"
          placeholder="输入新看板名称"
          @keyup.enter="handleCreateDashboard"
        />
        <el-button type="primary" :disabled="loading" @click="handleCreateDashboard">创建</el-button>
        <el-button @click="showNewDashboard = false">取消</el-button>
      </div>
      <div v-else class="flex gap-2">
        <el-select v-model="selectedDashboardId" class="flex-1" placeholder="请选择看板">
          <el-option v-if="dashboards.length === 0" value="" label="暂无看板，请先创建" disabled />
          <el-option v-for="d in dashboards" :key="d.id" :value="d.id" :label="d.name" />
        </el-select>
        <el-button @click="showNewDashboard = true">
          <i class="fa-solid fa-plus mr-1"></i>新建
        </el-button>
      </div>
    </div>

    <!-- 卡片配置区域 -->
    <template v-if="selectedDashboardId && !showNewDashboard">
      <!-- 图表类型 -->
      <div v-if="!hideChartType" class="mb-4 last:mb-0">
        <label class="block text-sm font-medium text-gray-700 mb-2">展示方式</label>
        <div class="flex gap-2">
          <button
            v-for="ct in CHART_TYPES"
            :key="ct.value"
            class="flex-1 flex flex-col items-center gap-1 px-2 py-3 border border-gray-200 rounded-lg bg-white text-gray-500 text-xs cursor-pointer transition-all duration-200 hover:border-[#1f82d9] hover:bg-[#f0f7ff] hover:text-[#1f82d9]"
            :class="{ 'border-[#1f82d9] bg-gradient-to-br from-[#e0f0ff] to-[#f0f7ff] text-[#1f82d9]': chartType === ct.value }"
            @click="chartType = ct.value"
          >
            <i :class="['fa-solid', ct.icon, 'text-base']"></i>
            <span>{{ ct.label }}</span>
          </button>
        </div>
      </div>

      <!-- SQL 预览 -->
      <div v-if="!hideSql" class="mb-4 last:mb-0">
        <label class="block text-sm font-medium text-gray-700 mb-2">SQL 查询</label>
        <div class="max-h-24 overflow-auto p-3 bg-gray-50 border border-gray-200 rounded-lg">
          <pre class="m-0 text-xs font-mono text-gray-700 whitespace-pre-wrap break-all">{{ sql }}</pre>
        </div>
      </div>
    </template>

    <template #footer>
      <el-button @click="handleClose">取消</el-button>
      <el-button
        type="primary"
        :disabled="loading || !selectedDashboardId"
        :loading="loading"
        @click="handleSave"
      >保存</el-button>
    </template>
  </el-dialog>
</template>
