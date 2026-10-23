<script setup lang="ts">
/**
 * 后台管理 · 单 Action Type 视图。
 * 对齐 Palantir Action Type View：Overview / Logic / Observability 三页。
 * 只读查看；编辑仍走 ActionTypeAdminPanel 现有表单。
 */
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getAdminOntologyActionType,
  getAdminOntologyActionTypeExecutions
} from '../../api'
import type { OntologyActionExecutionItem, OntologyActionTypeItem } from '../../types'

const props = defineProps({
  visible: { type: Boolean, default: false },
  actionType: { type: Object as PropType<OntologyActionTypeItem | null>, default: null }
})

const emit = defineEmits<{
  'update:visible': [value: boolean]
  edit: [item: OntologyActionTypeItem]
}>()

const loading = ref(false)
const error = ref('')
const detail = ref<OntologyActionTypeItem | null>(null)
const executions = ref<OntologyActionExecutionItem[]>([])
const executionsLoading = ref(false)
const executionsError = ref('')
const activeTab = ref<'overview' | 'logic' | 'observability'>('overview')

const STATUS_META = {
  draft: { label: '草稿', type: 'info' as const },
  experimental: { label: '实验', type: 'warning' as const },
  certified: { label: '认证', type: 'success' as const }
}
const statusMeta = (status: string) => STATUS_META[status as keyof typeof STATUS_META] || { label: status, type: 'info' as const }
const current = computed(() => detail.value || props.actionType)

const loadExecutions = async () => {
  const name = current.value?.action_type_name
  if (!name) return
  executionsLoading.value = true
  executionsError.value = ''
  try {
    const res = await getAdminOntologyActionTypeExecutions(name)
    if (res.status === 'success') executions.value = res.data.executions || []
    else executionsError.value = res.msg || '获取执行记录失败'
  } catch (err) {
    console.error('加载 Action 执行记录失败:', err)
    executionsError.value = '获取执行记录失败，请稍后重试'
  } finally {
    executionsLoading.value = false
  }
}

const loadDetail = async () => {
  const name = props.actionType?.action_type_name
  if (!name) return
  loading.value = true
  error.value = ''
  try {
    const res = await getAdminOntologyActionType(name)
    if (res.status === 'success') detail.value = res.data
    else error.value = res.msg || '获取 Action Type 详情失败'
  } catch (err) {
    console.error('加载 Action Type 详情失败:', err)
    error.value = '获取 Action Type 详情失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

const loadAll = async () => {
  activeTab.value = 'overview'
  detail.value = props.actionType
  await Promise.all([loadDetail(), loadExecutions()])
}

watch(
  () => props.visible,
  visible => {
    if (visible) void loadAll()
  }
)

const close = () => emit('update:visible', false)
const edit = () => {
  if (!current.value) return
  emit('edit', current.value)
}
const editValueText = (edit: Record<string, unknown>) => {
  const value = (edit.value || {}) as Record<string, unknown>
  if (value.kind === 'parameter') return `参数：${String(value.parameter || '-')}`
  return `静态值：${value.value == null ? '-' : JSON.stringify(value.value)}`
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    width="min(980px, 94vw)"
    destroy-on-close
    append-to-body
    @update:model-value="(value: boolean) => emit('update:visible', value)"
  >
    <template #header>
      <div class="flex items-center gap-2">
        <i class="fa-solid fa-bolt text-[11px] text-amber-500"></i>
        <span class="text-base font-medium text-slate-800">{{ current?.action_type_name || 'Action Type' }}</span>
        <el-tag v-if="current" size="small" :type="statusMeta(current.status).type" effect="plain">
          {{ statusMeta(current.status).label }}
        </el-tag>
        <el-button v-if="current" size="small" class="ml-auto" @click="edit">编辑定义</el-button>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-3" />
    <div v-loading="loading" class="min-h-[420px]">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="Overview" name="overview">
          <div class="grid grid-cols-2 gap-x-8 gap-y-2 text-xs text-slate-500">
            <div>API 名：<span class="font-mono text-slate-800">{{ current?.action_type_name || '-' }}</span></div>
            <div>展示名：<span class="text-slate-800">{{ current?.display_name || '-' }}</span></div>
            <div>目标对象类型：<span class="font-mono text-slate-800">{{ current?.object_type_name || '-' }}</span></div>
            <div>状态：<span class="text-slate-800">{{ statusMeta(current?.status || '').label }}</span></div>
            <div class="col-span-2">描述：<span class="text-slate-800">{{ current?.description || '-' }}</span></div>
          </div>
          <div class="mt-5 grid grid-cols-2 gap-3">
            <div class="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <div class="text-[11px] text-slate-400">参数</div>
              <div class="mt-1 text-xl font-medium text-slate-800">{{ (current?.parameters || []).length }}</div>
            </div>
            <div class="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <div class="text-[11px] text-slate-400">属性编辑</div>
              <div class="mt-1 text-xl font-medium text-slate-800">{{ (current?.edits || []).length }}</div>
            </div>
          </div>
          <div class="mt-4 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-700">
            当前执行只写对象属性覆盖层与审计，不直接修改源数据；执行开关由
            <code>CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED</code> 控制。
          </div>
        </el-tab-pane>

        <el-tab-pane label="Logic" name="logic">
          <div class="mb-2 text-xs font-medium text-slate-600">参数定义</div>
          <el-table :data="current?.parameters || []" size="small" border empty-text="暂无参数">
            <el-table-column prop="name" label="参数" min-width="150" />
            <el-table-column prop="type" label="类型" width="100" />
            <el-table-column label="必填" width="70" align="center">
              <template #default="{ row }">{{ row.required ? '是' : '否' }}</template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip />
          </el-table>
          <div class="mb-2 mt-4 text-xs font-medium text-slate-600">属性编辑</div>
          <el-table :data="current?.edits || []" size="small" border empty-text="暂无编辑">
            <el-table-column prop="property" label="属性" min-width="150" />
            <el-table-column label="操作" width="110">
              <template #default="{ row }">{{ row.operation }}</template>
            </el-table-column>
            <el-table-column label="值来源" min-width="220">
              <template #default="{ row }">{{ editValueText(row) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="Observability" name="observability">
          <div class="mb-2 flex items-center gap-2">
            <span class="text-xs text-slate-500">最近执行记录</span>
            <el-button size="small" :loading="executionsLoading" @click="loadExecutions">刷新</el-button>
          </div>
          <el-alert v-if="executionsError" :title="executionsError" type="error" :closable="false" show-icon class="mb-2" />
          <el-table v-loading="executionsLoading" :data="executions" size="small" border empty-text="暂无执行记录">
            <el-table-column type="expand">
              <template #default="{ row }">
                <pre class="m-0 overflow-auto rounded bg-slate-50 p-2 text-[11px] text-slate-600">{{ JSON.stringify({ parameters: row.parameters, edits: row.edits }, null, 2) }}</pre>
              </template>
            </el-table-column>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="create_time" label="时间" width="160" />
            <el-table-column prop="object_key" label="对象主键" min-width="150" show-overflow-tooltip />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'succeeded' ? 'success' : 'danger'" effect="plain">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="create_by_name" label="操作人" width="120" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>

    <template #footer>
      <el-button @click="close">关闭</el-button>
    </template>
  </el-dialog>
</template>
