<script setup lang="ts">
/**
 * 会话列表侧边栏
 * 用于智能问数模式，包含指标和会话两个 tab
 * 看板已提取为独立的 DashboardPanel 组件
 */
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSemanticConfigs, createSemanticConfig, updateSemanticConfig, deleteSemanticConfig } from '../../api'
import type { SemanticConfigItem } from '../../types'
import SemanticDatasetPanel from './SemanticDatasetPanel.vue'

interface ConversationItem {
  id: string
  title: string
  created_at: string
}

interface ConversationGroup {
  label: string
  items: ConversationItem[]
}

const CONVERSATION_ICON_POOL = [
  { icon: 'fa-comment-dots', color: '#B3DBD2' }, // 普通对话
  { icon: 'fa-chart-line', color: '#B3DBD2' },   // 聚合查询
  { icon: 'fa-table', color: '#B3DBD2' },        // 明细查询
] as const

const getConversationIconMeta = (conversationId: string | undefined) => {
  const id = conversationId || ''
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0
  }
  return CONVERSATION_ICON_POOL[hash % CONVERSATION_ICON_POOL.length] || CONVERSATION_ICON_POOL[0]
}

interface Props {
  activeTab: 'metrics' | 'conversation'
  groupedConversations?: ConversationGroup[]
  currentConversationId?: string | null
  isLoading?: boolean
  showNewConversationButton?: boolean
  datasourceId?: string
  semanticConfigs?: SemanticConfigItem[]
  loadingConfigs?: boolean
  bootstrapLoading?: boolean
  selectedDatasetId?: string | null
  hideManagement?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  groupedConversations: () => [],
  currentConversationId: null,
  isLoading: false,
  showNewConversationButton: true,
  datasourceId: '',
  semanticConfigs: undefined,
  loadingConfigs: undefined,
  bootstrapLoading: false,
  selectedDatasetId: null,
  hideManagement: false
})

const emit = defineEmits<{
  selectConversation: [conversationId: string]
  newConversation: []
  selectDataset: [configId: string | null]
  refreshSemanticConfigs: []
}>()

// 数据集列表
const internalSemanticConfigs = ref<SemanticConfigItem[]>([])
const internalIsLoadingConfigs = ref(false)
const semanticConfigs = computed(() => props.semanticConfigs ?? internalSemanticConfigs.value)
const isLoadingConfigs = computed(() => props.loadingConfigs ?? internalIsLoadingConfigs.value)

// 选中的数据集 ID
const selectedConfigId = ref<string | null>(null)

// 当前选中的数据集
const selectedConfig = computed(() => {
  if (!selectedConfigId.value) return null
  return semanticConfigs.value.find(c => c.id === selectedConfigId.value) || null
})

// 数据集管理弹窗
const showDatasetManager = ref(false)

const syncSelectedConfig = () => {
  const configs = semanticConfigs.value
  if (configs.length === 0) {
    if (selectedConfigId.value !== null) {
      selectedConfigId.value = null
      emit('selectDataset', null)
    }
    return
  }
  if (selectedConfigId.value && configs.find(c => c.id === selectedConfigId.value)) {
    return
  }
  const enabledConfig = configs.find(c => c.enabled)
  const nextId = enabledConfig?.id || configs[0]?.id || null
  if (nextId !== selectedConfigId.value) {
    selectedConfigId.value = nextId
    emit('selectDataset', nextId)
  }
}

// 加载语义配置列表
const loadSemanticConfigs = async () => {
  if (props.semanticConfigs !== undefined) {
    emit('refreshSemanticConfigs')
    return
  }
  if (!props.datasourceId) return
  internalIsLoadingConfigs.value = true
  try {
    const res = await getSemanticConfigs(props.datasourceId)
    if (res.status === 'success') {
      internalSemanticConfigs.value = res.data?.configs || []
      syncSelectedConfig()
    }
  } catch (err) {
    console.error('加载语义配置失败:', err)
  } finally {
    internalIsLoadingConfigs.value = false
  }
}

// 创建新数据集
const handleCreateConfig = async () => {
  if (!props.datasourceId) return
  try {
    const res = await createSemanticConfig(props.datasourceId, {
      name: `数据集 ${semanticConfigs.value.length + 1}`,
      enabled: true,
      metrics: [],
      dimensions: [],
      relationships: [],
    })
    if (res.status === 'success' && res.data) {
      internalSemanticConfigs.value = [...semanticConfigs.value, res.data]
      // 自动选中新创建的
      handleSelectConfig(res.data.id)
      ElMessage.success('数据集创建成功')
    }
  } catch (err) {
    ElMessage.error('创建失败')
  }
}

// 选择数据集
const handleSelectConfig = (configId: string) => {
  selectedConfigId.value = configId
  emit('selectDataset', configId)
  showDatasetManager.value = false
}

// 删除数据集
const handleDeleteConfig = async (configId: string, configName: string) => {
  try {
    await ElMessageBox.confirm(`确定要删除"${configName}"吗？`, '确认删除', { type: 'warning' })
    await deleteSemanticConfig(props.datasourceId, configId)
    internalSemanticConfigs.value = semanticConfigs.value.filter(c => c.id !== configId)
    // 如果删除的是选中的，重新选择
    if (selectedConfigId.value === configId) {
      const next = semanticConfigs.value.find(c => c.enabled) || semanticConfigs.value[0]
      selectedConfigId.value = next?.id || null
      emit('selectDataset', selectedConfigId.value)
    }
    ElMessage.success('删除成功')
  } catch (err: any) {
    if (err !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

// 编辑数据集名称
const handleEditConfigName = async (configId: string, currentName: string) => {
  try {
    const { value: newName } = await ElMessageBox.prompt('请输入数据集名称', '编辑名称', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: currentName,
      inputPattern: /\S+/,
      inputErrorMessage: '名称不能为空',
    })
    if (newName && newName !== currentName) {
      const res = await updateSemanticConfig(props.datasourceId, configId, { name: newName })
      if (res.status === 'success') {
        const config = internalSemanticConfigs.value.find(c => c.id === configId)
        if (config) config.name = newName
        ElMessage.success('名称更新成功')
      }
    }
  } catch (err: any) {
    if (err !== 'cancel') ElMessage.error('更新失败')
  }
}


// 监听数据源变化
watch(() => props.datasourceId, () => {
  selectedConfigId.value = null
  if (props.semanticConfigs === undefined) {
    loadSemanticConfigs()
    return
  }
  syncSelectedConfig()
}, { immediate: true })

watch(() => props.semanticConfigs, () => {
  if (props.semanticConfigs === undefined) return
  syncSelectedConfig()
}, { immediate: true, deep: true })

// 父子同步：父层数据集变化时，同步当前选中数据集
watch(() => props.selectedDatasetId, (newId) => {
  if (newId === selectedConfigId.value) return
  if (!newId) {
    selectedConfigId.value = null
    return
  }
    if (semanticConfigs.value.find(c => c.id === newId)) {
      selectedConfigId.value = newId
    }
}, { immediate: true })

</script>

<template>
  <div class="flex flex-col h-full overflow-hidden">
    <!-- ==================== 会话 Tab ==================== -->
    <template v-if="activeTab === 'conversation'">
      <!-- 新建会话按钮 -->
      <div v-if="showNewConversationButton" class="p-4">
        <button
          class="w-[82%] mx-auto flex items-center justify-center gap-2 px-5 py-3 border-none rounded-lg bg-gradient-to-br from-[#1f82d9] to-[#135589] text-white text-sm font-medium cursor-pointer transition-all duration-200 shadow-[0_2px_8px_rgba(31,130,217,0.3)] hover:-translate-y-0.5 hover:shadow-[0_4px_12px_rgba(31,130,217,0.4)]"
          @click="emit('newConversation')"
        >
          <i class="fa-solid fa-plus"></i>
          <span>新建会话</span>
        </button>
      </div>

      <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto px-4 pb-4 scrollbar-thin scrollbar-thumb-black/10">
        <div v-if="!isLoading && groupedConversations.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
          <i class="fa-regular fa-message text-2xl opacity-50"></i>
          <span>暂无会话</span>
        </div>

        <template v-for="group in groupedConversations" :key="group.label">
          <div class="mb-2">
            <div class="py-2 text-xs text-gray-400 font-medium uppercase tracking-wide">{{ group.label }}</div>
            <div
              v-for="conv in group.items"
              :key="conv.id"
              class="flex items-center gap-2 py-3 cursor-pointer transition-all duration-150"
              :class="currentConversationId === conv.id ? 'text-blue-500 font-medium' : 'text-gray-800 hover:text-blue-500'"
              @click="emit('selectConversation', conv.id)"
            >
              <i
                :class="['fa-solid text-xs shrink-0', getConversationIconMeta(conv.id).icon]"
                :style="{ color: getConversationIconMeta(conv.id).color }"
              ></i>
              <span class="flex-1 text-sm overflow-hidden text-ellipsis whitespace-nowrap leading-relaxed">{{ conv.title }}</span>
            </div>
          </div>
        </template>
      </div>
    </template>

    <!-- ==================== 指标 Tab ==================== -->
    <template v-else-if="activeTab === 'metrics'">
      <!-- 加载状态 -->
      <div v-if="bootstrapLoading || isLoadingConfigs" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
        <i class="fa-solid fa-spinner fa-spin text-2xl opacity-50"></i>
        <span>加载中...</span>
      </div>

      <div v-else-if="semanticConfigs.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
        <i class="fa-solid fa-database text-2xl opacity-50"></i>
        <span>暂无数据集</span>
      </div>

      <div v-else-if="!selectedConfig" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-xs">
        <i class="fa-solid fa-spinner fa-spin text-2xl opacity-50"></i>
        <span>正在准备数据集...</span>
      </div>

      <div v-else class="flex-1 min-h-0 overflow-hidden">
        <SemanticDatasetPanel
          :semantic-config="selectedConfig"
          :semantic-configs="semanticConfigs"
          :selected-dataset-id="selectedConfigId"
          :loading="isLoadingConfigs"
          @dataset-change="handleSelectConfig"
          @refresh="loadSemanticConfigs"
        />
      </div>

      <!-- 数据集管理弹窗 -->
      <el-dialog v-model="showDatasetManager" width="400px" :show-close="false" append-to-body destroy-on-close>
        <template #header>
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold text-gray-800 m-0">数据集管理</h3>
            <button
              class="w-7 h-7 flex items-center justify-center border-none rounded-md bg-transparent text-gray-400 cursor-pointer text-xs transition-all duration-200 hover:bg-[#f0f1f3] hover:text-gray-600"
              @click="showDatasetManager = false"
            >
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
        </template>

        <!-- 数据集列表 -->
        <div class="overflow-y-auto" style="max-height: 50vh;">
          <div v-if="semanticConfigs.length === 0" class="flex flex-col items-center justify-center gap-2 py-8 text-gray-400 text-xs">
            <i class="fa-solid fa-database text-2xl opacity-50"></i>
            <span>暂无数据集，请创建</span>
          </div>

          <div
            v-for="config in semanticConfigs"
            :key="config.id"
            :class="[
              'group flex items-center justify-between px-3.5 py-3 rounded-lg mb-2 last:mb-0 cursor-pointer transition-all duration-200 border',
              selectedConfigId === config.id
                ? 'border-blue-300 bg-blue-50/60'
                : 'border-gray-200 bg-white hover:border-blue-200 hover:bg-blue-50/30'
            ]"
            @click="handleSelectConfig(config.id)"
          >
            <div class="flex items-center gap-2.5 flex-1 min-w-0">
              <!-- 选中指示 -->
              <div
                :class="[
                  'w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 transition-all duration-200',
                  selectedConfigId === config.id ? 'border-blue-500 bg-blue-500' : 'border-gray-300'
                ]"
              >
                <i v-if="selectedConfigId === config.id" class="fa-solid fa-check text-[8px] text-white"></i>
              </div>
              <div class="flex flex-col gap-0.5 min-w-0">
                <span class="text-[13px] font-medium text-gray-800 overflow-hidden text-ellipsis whitespace-nowrap">{{ config.name }}</span>
                <span class="text-[11px] text-gray-400">
                  {{ config.metrics?.length || 0 }}指标 · {{ config.dimensions?.length || 0 }}维度 · {{ (config.relationships?.length || 0) }}关系
                </span>
              </div>
            </div>
            <!-- 操作 -->
            <div class="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity duration-200" @click.stop>
              <button
                class="w-6 h-6 flex items-center justify-center border-none rounded bg-transparent text-gray-400 cursor-pointer text-[10px] transition-all duration-200 hover:text-blue-500 hover:bg-blue-500/10"
                title="编辑名称"
                @click="handleEditConfigName(config.id, config.name)"
              >
                <i class="fa-solid fa-pen"></i>
              </button>
              <button
                class="w-6 h-6 flex items-center justify-center border-none rounded bg-transparent text-gray-400 cursor-pointer text-[10px] transition-all duration-200 hover:text-red-500 hover:bg-red-500/10"
                title="删除"
                @click="handleDeleteConfig(config.id, config.name)"
              >
                <i class="fa-solid fa-trash"></i>
              </button>
            </div>
          </div>
        </div>

        <template #footer>
          <button
            class="w-full flex items-center justify-center gap-2 px-4 py-2.5 border-2 border-dashed border-gray-300 rounded-lg bg-transparent text-gray-500 text-[13px] cursor-pointer transition-all duration-200 hover:border-blue-400 hover:text-blue-500 hover:bg-blue-50/50"
            @click="handleCreateConfig"
          >
            <i class="fa-solid fa-plus"></i>
            <span>新建数据集</span>
          </button>
        </template>
      </el-dialog>

    </template>
  </div>
</template>
