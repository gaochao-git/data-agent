<script setup lang="ts">
/**
 * 看板面板（公用侧边栏组件）
 * 从 ConversationSidebar 提取，三个模式共享
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createDashboard, deleteDashboard, type Dashboard } from '../../api/dashboard'

interface Props {
  selectedDashboardId?: string | null
  dashboards?: Dashboard[]
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  selectedDashboardId: null,
  dashboards: () => [],
  loading: false,
})

const emit = defineEmits<{
  openDashboard: [dashboardId: string]
  dashboardsChange: [dashboards: Dashboard[]]
}>()

// 看板相关状态
const isCreatingDashboard = ref(false)
const newDashboardName = ref('')
const newDashboardDesc = ref('')
const dashboards = computed(() => props.dashboards)
const isLoadingDashboards = computed(() => props.loading)

// 创建看板
const handleCreateDashboard = async () => {
  if (!newDashboardName.value.trim()) {
    ElMessage.warning('请输入看板名称')
    return
  }
  try {
    const newDashboard = await createDashboard({
      name: newDashboardName.value,
      description: newDashboardDesc.value || undefined,
    })
    emit('dashboardsChange', [newDashboard, ...dashboards.value])
    newDashboardName.value = ''
    newDashboardDesc.value = ''
    isCreatingDashboard.value = false
    ElMessage.success('看板创建成功')
    emit('openDashboard', newDashboard.id)
  } catch (err) {
    console.error('创建看板失败:', err)
    ElMessage.error('创建看板失败')
  }
}

// 删除看板
const handleDeleteDashboard = async (dashboardId: string, event: Event) => {
  event.stopPropagation()
  try {
    const dashboard = dashboards.value.find(d => d.id === dashboardId)
    await ElMessageBox.confirm(
      `确定删除看板“${dashboard?.name || '未命名看板'}”吗？`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      }
    )
    await deleteDashboard(dashboardId)
    emit('dashboardsChange', dashboards.value.filter(d => d.id !== dashboardId))
    ElMessage.success('看板已删除')
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    console.error('删除看板失败:', err)
    ElMessage.error('删除看板失败')
  }
}
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden">
    <!-- 新建看板按钮 -->
    <div class="sidebar-baseline-target">
      <button
        class="w-full h-[28px] flex items-center justify-center gap-1.5 px-3 border-none rounded-md bg-gradient-to-br from-blue-500 to-cyan-500 text-white text-[12px] font-medium cursor-pointer transition-all duration-200 shadow-[0_2px_8px_rgba(59,130,246,0.3)] hover:-translate-y-0.5 hover:shadow-[0_4px_12px_rgba(59,130,246,0.4)]"
        @click="isCreatingDashboard = true"
      >
        <i class="fa-solid fa-plus"></i>
        <span>新建看板</span>
      </button>
    </div>

    <!-- 新建看板表单 -->
    <div v-if="isCreatingDashboard" class="px-4 py-3 bg-blue-50 border-b border-blue-100">
      <div class="mb-3">
        <label class="block text-[11px] text-gray-500 mb-1">看板名称</label>
        <input
          v-model="newDashboardName"
          type="text"
          placeholder="请输入看板名称"
          class="w-full px-3 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-all duration-200 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
        />
      </div>
      <div class="mb-3">
        <label class="block text-[11px] text-gray-500 mb-1">描述（可选）</label>
        <textarea
          v-model="newDashboardDesc"
          placeholder="请输入看板描述"
          rows="2"
          class="w-full px-3 py-2 border border-gray-300 rounded-md text-[13px] outline-none transition-all duration-200 resize-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
        ></textarea>
      </div>
      <div class="flex gap-2">
        <button
          class="flex-1 px-3 py-2 border-none rounded-md text-[13px] cursor-pointer transition-all duration-200 bg-blue-500 text-white hover:bg-blue-600"
          @click="handleCreateDashboard"
        >创建</button>
        <button
          class="flex-1 px-3 py-2 border border-gray-300 rounded-md text-[13px] cursor-pointer transition-all duration-200 bg-white text-gray-700 hover:bg-gray-50"
          @click="isCreatingDashboard = false; newDashboardName = ''; newDashboardDesc = ''"
        >取消</button>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="isLoadingDashboards" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
      <i class="fa-solid fa-spinner fa-spin text-2xl opacity-50"></i>
      <span>加载中...</span>
    </div>

    <!-- 看板列表 -->
    <div v-else class="flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin scrollbar-thumb-black/10">
      <div v-if="dashboards.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
        <i class="fa-solid fa-chart-pie text-2xl opacity-50"></i>
        <span>暂无看板</span>
      </div>
      <template v-else>
        <div class="py-2 text-xs text-gray-400 font-medium uppercase tracking-wide">我的看板</div>
        <div
          v-for="dashboard in dashboards"
          :key="dashboard.id"
          :class="[
            'group p-3 mb-2 rounded-lg cursor-pointer transition-all duration-200',
            selectedDashboardId === dashboard.id
              ? 'bg-blue-50 border border-blue-300 shadow-[0_2px_8px_rgba(59,130,246,0.15)]'
              : 'bg-white border border-gray-200 hover:border-blue-300 hover:shadow-[0_2px_8px_rgba(0,0,0,0.06)]'
          ]"
          @click="emit('openDashboard', dashboard.id)"
        >
          <div class="flex items-start justify-between mb-1">
            <div :class="['text-sm font-medium leading-snug', selectedDashboardId === dashboard.id ? 'text-blue-700' : 'text-gray-800']">{{ dashboard.name }}</div>
            <button
              class="px-1.5 py-1 border-none bg-transparent text-gray-300 cursor-pointer rounded opacity-0 transition-all duration-200 group-hover:opacity-100 hover:bg-red-100 hover:text-red-500"
              @click="handleDeleteDashboard(dashboard.id, $event)"
              title="删除看板"
            >
              <i class="fa-solid fa-trash"></i>
            </button>
          </div>
          <div v-if="dashboard.description" class="text-xs text-gray-500 mb-2 line-clamp-2">{{ dashboard.description }}</div>
          <div class="flex items-center justify-between text-[11px] text-gray-400">
            <span>{{ dashboard.create_time?.split('T')[0] || '' }}</span>
            <span class="flex items-center gap-1">
              <i class="fa-solid fa-chart-pie"></i>
              {{ dashboard.panel_count || 0 }} 个图表
            </span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
