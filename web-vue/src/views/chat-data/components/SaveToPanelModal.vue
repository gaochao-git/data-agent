<script setup lang="ts">
/**
 * 保存到看板的弹窗组件
 * 将生成的 SQL 保存到分析大屏的面板中
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { listDashboards, addPanel, createDashboard, generatePanelName, type Dashboard } from '../api/dashboard'
import { CHART_TYPES, CHART_TYPE_HELP_TEXT, normalizeChartType, type ChartType } from '../chartTypes'
import type { ExecutionTargetOption } from '../types'

interface Props {
  visible: boolean
  sql: string
  chartType?: string
  chartConfig?: Record<string, unknown>
  defaultName?: string
  sourceThreadId?: string
  datasourceId?: string
  memberDatasourceId?: string
  memberDatasourceIds?: string[]
  executionTargets?: ExecutionTargetOption[]
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
const chartType = ref<ChartType>(normalizeChartType(props.chartType))
const loading = ref(false)
const showNewDashboard = ref(false)
const newDashboardName = ref('')
const selectedMemberDatasourceIds = ref<string[]>([])

const canUseMultiSource = computed(() => (props.executionTargets?.length || 0) > 1)
const selectedMemberDatasourceId = computed(() => selectedMemberDatasourceIds.value[0] || props.memberDatasourceId || '')
const singleSelectedMemberDatasourceId = computed({
  get: () => selectedMemberDatasourceId.value,
  set: (value: string) => {
    selectedMemberDatasourceIds.value = value ? [value] : []
  }
})

const normalizeMemberIds = (ids: unknown): string[] => {
  const result: string[] = []
  if (!Array.isArray(ids)) return result
  ids.forEach(item => {
    const memberId = String(item || '').trim()
    if (memberId && !result.includes(memberId)) {
      result.push(memberId)
    }
  })
  return result
}

const resetSelectedMembers = () => {
  const availableIds = (props.executionTargets || []).map(item => item.member_datasource_id)
  const defaultIds = normalizeMemberIds(props.memberDatasourceIds?.length ? props.memberDatasourceIds : [props.memberDatasourceId])
    .filter(item => !availableIds.length || availableIds.includes(item))
  selectedMemberDatasourceIds.value = defaultIds.length ? defaultIds : (props.memberDatasourceId ? [props.memberDatasourceId] : [])
}

const getExecutionTargetName = (target: ExecutionTargetOption) => target.org_name || target.label || target.member_datasource_id
const allExecutionTargetIds = computed(() => (props.executionTargets || []).map(item => item.member_datasource_id).filter(Boolean))
const isAllExecutionTargetsSelected = computed(() => (
  allExecutionTargetIds.value.length > 0 && allExecutionTargetIds.value.every(id => selectedMemberDatasourceIds.value.includes(id))
))
const selectAllExecutionTargets = () => {
  selectedMemberDatasourceIds.value = [...allExecutionTargetIds.value]
}
const clearExecutionTargets = () => {
  selectedMemberDatasourceIds.value = []
}

// 图表类型选项
const chartTypeOptions = CHART_TYPES.map(item => ({ value: item.key, label: item.label, icon: item.icon }))
const currentChartTypeMeta = computed(() => (
  CHART_TYPES.find(item => item.key === chartType.value) ?? CHART_TYPES[0]
))
const currentChartTypeHelp = computed(() => (
  CHART_TYPE_HELP_TEXT[chartType.value] ?? CHART_TYPE_HELP_TEXT.table
))

// 监听 visible 变化加载看板列表
watch(
  () => props.visible,
  async (visible) => {
    if (visible) {
      chartType.value = normalizeChartType(props.chartType)
      resetSelectedMembers()
      await loadDashboards()
    }
  }
)

watch(chartType, () => {
  if (!canUseMultiSource.value && selectedMemberDatasourceIds.value.length > 1) {
    selectedMemberDatasourceIds.value = selectedMemberDatasourceIds.value.slice(0, 1)
  }
})

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
    if ((props.executionTargets?.length || 0) > 0 && selectedMemberDatasourceIds.value.length === 0) {
      ElMessage.warning('请选择执行对象')
      return
    }
    const titleResult = await generatePanelName({
      name: props.defaultName,
      sql: props.sql,
      chart_type: chartType.value
    })
    const selectedIds = canUseMultiSource.value ? selectedMemberDatasourceIds.value : selectedMemberDatasourceIds.value.slice(0, 1)
    const { multiSource: _oldMultiSource, ...baseChartConfig } = props.chartConfig || {}
    const chartConfig = {
      ...baseChartConfig,
      ...(selectedIds.length > 1
        ? { multiSource: { enabled: true, memberDatasourceIds: selectedIds } }
        : {})
    }
    await addPanel(selectedDashboardId.value, {
      name: titleResult.name,
      sql: props.sql,
      chart_type: chartType.value,
      chart_config: chartConfig,
      source_thread_id: props.sourceThreadId,
      datasource_id: props.datasourceId,
      member_datasource_id: selectedIds[0] || selectedMemberDatasourceId.value
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

const handleCopySql = async () => {
  const text = props.sql || ''
  if (!text.trim()) {
    ElMessage.warning('暂无可复制的 SQL')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('SQL 已复制')
  } catch (err) {
    ElMessage.error('复制 SQL 失败')
    console.error(err)
  }
}

// 关闭弹窗
const handleClose = () => {
  emit('update:visible', false)
  emit('close')
}
</script>

<template>
  <el-dialog :model-value="visible" title="保存到看板" width="720px" append-to-body :z-index="4000" @close="handleClose">
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
      <!-- SQL 预览 -->
      <div v-if="!hideSql" class="mb-4 last:mb-0">
        <div class="mb-2 flex items-center justify-between gap-3">
          <label class="block text-sm font-medium text-gray-700">SQL 查询</label>
          <button
            type="button"
            class="inline-flex items-center gap-1.5 border-none bg-transparent p-0 text-xs text-gray-500 cursor-pointer transition-colors duration-150 hover:text-[#1f82d9]"
            title="复制 SQL"
            @click="handleCopySql"
          >
            <i class="fa-regular fa-copy"></i>
            <span>复制SQL</span>
          </button>
        </div>
        <div class="max-h-[220px] min-h-[180px] overflow-auto p-3 bg-gray-50 border border-gray-200 rounded-lg">
          <pre class="m-0 text-xs font-mono leading-relaxed text-gray-700 whitespace-pre-wrap break-all">{{ sql }}</pre>
        </div>
      </div>

      <div v-if="executionTargets?.length" class="mb-4 last:mb-0">
        <div class="mb-2 flex items-center justify-between gap-3">
          <label class="block text-sm font-medium text-gray-700">执行对象</label>
          <div v-if="canUseMultiSource" class="flex items-center gap-3 text-xs">
            <span class="text-gray-400">已选 {{ selectedMemberDatasourceIds.length }}/{{ executionTargets.length }}</span>
            <button
              type="button"
              class="border-none bg-transparent p-0 text-[#1f82d9] cursor-pointer hover:text-[#0f5fa8]"
              @click="isAllExecutionTargetsSelected ? clearExecutionTargets() : selectAllExecutionTargets()"
            >
              {{ isAllExecutionTargetsSelected ? '取消全选' : '全选' }}
            </button>
          </div>
        </div>
        <el-checkbox-group
          v-if="canUseMultiSource"
          v-model="selectedMemberDatasourceIds"
          class="grid max-h-[132px] grid-cols-3 gap-2 overflow-auto rounded-lg border border-gray-200 p-2"
        >
          <el-checkbox
            v-for="target in executionTargets"
            :key="target.member_datasource_id"
            :value="target.member_datasource_id"
            border
            class="!mr-0"
          >
            <span class="inline-block max-w-[140px] truncate align-bottom" :title="getExecutionTargetName(target)">
              {{ getExecutionTargetName(target) }}
            </span>
          </el-checkbox>
        </el-checkbox-group>
        <el-radio-group v-else v-model="singleSelectedMemberDatasourceId" class="grid max-h-[132px] grid-cols-3 gap-2 overflow-auto rounded-lg border border-gray-200 p-2">
          <el-radio
            v-for="target in executionTargets"
            :key="target.member_datasource_id"
            :value="target.member_datasource_id"
            border
            class="!mr-0"
          >
            <span class="inline-block max-w-[140px] truncate align-bottom" :title="getExecutionTargetName(target)">
              {{ getExecutionTargetName(target) }}
            </span>
          </el-radio>
        </el-radio-group>
      </div>

      <!-- 图表类型 -->
      <div v-if="!hideChartType" class="mb-4 last:mb-0">
        <label class="block text-sm font-medium text-gray-700 mb-2">展示方式</label>
        <div class="grid grid-cols-5 gap-2">
          <button
            v-for="ct in chartTypeOptions"
            :key="ct.value"
            class="flex min-h-[46px] flex-col items-center justify-center gap-0.5 px-2 py-1 border border-gray-200 rounded-md bg-white text-gray-500 text-xs cursor-pointer transition-all duration-200 hover:border-[#1f82d9] hover:bg-[#f0f7ff] hover:text-[#1f82d9]"
            :class="{ 'border-[#1f82d9] bg-gradient-to-br from-[#e0f0ff] to-[#f0f7ff] text-[#1f82d9]': chartType === ct.value }"
            @click="chartType = ct.value"
          >
            <i :class="['fa-solid', ct.icon, 'text-xs']"></i>
            <span class="text-center leading-4 text-[11px]">{{ ct.label }}</span>
          </button>
        </div>
        <div class="mt-3 rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2.5">
          <div class="flex items-center gap-2 text-[12px] font-medium text-blue-700">
            <i :class="['fa-solid', currentChartTypeMeta.icon]"></i>
            <span>{{ currentChartTypeMeta.label }}</span>
          </div>
          <div class="mt-1 text-[12px] leading-5 text-blue-700/90">{{ currentChartTypeHelp.summary }}</div>
          <div v-for="(item, idx) in currentChartTypeHelp.examples" :key="idx" class="mt-1">
            <div class="text-[12px] leading-5 text-blue-600">场景{{ currentChartTypeHelp.examples.length > 1 ? ` ${idx + 1}` : '' }}：{{ item.scene }}</div>
            <div class="mt-0.5 flex min-w-0 items-start gap-1.5 text-[12px] leading-5 text-blue-600">
              <span class="shrink-0">SQL：</span>
              <code class="min-w-0 flex-1 whitespace-normal break-words rounded bg-white/80 px-1.5 py-0.5 font-mono text-[11px] text-blue-700">{{ item.sql }}</code>
            </div>
          </div>
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
