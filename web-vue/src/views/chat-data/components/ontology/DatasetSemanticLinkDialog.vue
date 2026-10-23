<script setup lang="ts">
/**
 * Dataset ↔ 语义模型关联管理（Ossie mapping 简化形态）。
 *
 * 只维护「引用 + 列对照」：指标口径唯一权威仍在语义层，本弹窗不编辑指标表达式。
 * 入口：本体对象类型 Overview 的「语义指标」区（Dataset 维度管理）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  deleteAdminDatasetSemanticLink,
  getAdminDataset,
  getAdminSemanticConfigs,
  listAdminDatasetSemanticLinks,
  upsertAdminDatasetSemanticLink
} from '../../api'
import type { DatasetSemanticLinkItem, LogicalDatasetItem, SemanticConfigItem } from '../../types'

interface LinkForm {
  datasourceGroupId: string
  semanticConfigId: string
  tableName: string
  metricScope: string[]
  columnMappingsText: string
  lifecycleStatus: 'draft' | 'active'
}

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  datasetName: { type: String, default: '' },
  datasourceGroupId: { type: String, default: '' }
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  changed: []
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value)
})

const loading = ref(false)
const links = ref<DatasetSemanticLinkItem[]>([])
const dataset = ref<LogicalDatasetItem | null>(null)
const configs = ref<SemanticConfigItem[]>([])
const configsLoading = ref(false)
const formVisible = ref(false)
const saving = ref(false)
const isEdit = ref(false)

const form = ref<LinkForm>({
  datasourceGroupId: '',
  semanticConfigId: '',
  tableName: '',
  metricScope: [],
  columnMappingsText: '{}',
  lifecycleStatus: 'draft'
})

const groupOptions = computed(() => {
  const groups = (dataset.value?.bindings || []).map(binding => binding.datasource_group_id).filter(Boolean)
  return [...new Set(groups)]
})

const tableOptions = computed(() => {
  const tables = new Set<string>()
  if (dataset.value?.dataset_name) tables.add(dataset.value.dataset_name)
  for (const binding of dataset.value?.bindings || []) {
    if (!form.value.datasourceGroupId || binding.datasource_group_id === form.value.datasourceGroupId) {
      if (binding.physical_table) tables.add(binding.physical_table)
    }
  }
  return [...tables]
})

const selectedConfig = computed(
  () => configs.value.find(config => config.id === form.value.semanticConfigId) || null
)
const metricOptions = computed(() => (selectedConfig.value?.metrics || []).map(metric => metric.name))

const statusMeta: Record<string, { label: string; tagType: 'info' | 'success' }> = {
  draft: { label: '草稿', tagType: 'info' },
  active: { label: '启用', tagType: 'success' }
}

const resetForm = () => {
  form.value = {
    datasourceGroupId: props.datasourceGroupId || groupOptions.value[0] || '',
    semanticConfigId: '',
    tableName: dataset.value?.dataset_name || '',
    metricScope: [],
    columnMappingsText: '{}',
    // 默认启用：新建后即可在对象作用域执行指标；需要先审核时再显式改草稿
    lifecycleStatus: 'active'
  }
}

const loadConfigs = async () => {
  if (!form.value.datasourceGroupId) {
    configs.value = []
    return
  }
  configsLoading.value = true
  try {
    const res = await getAdminSemanticConfigs(form.value.datasourceGroupId)
    configs.value = res.status === 'success' ? res.data?.configs || [] : []
  } catch (err) {
    console.error('加载语义模型列表失败:', err)
    configs.value = []
  } finally {
    configsLoading.value = false
  }
}

const loadDataset = async () => {
  if (!props.datasetName) return
  try {
    const res = await getAdminDataset(props.datasetName)
    dataset.value = res.status === 'success' ? res.data : null
  } catch (err) {
    console.error('加载 Dataset 详情失败:', err)
    dataset.value = null
  }
}

const loadLinks = async () => {
  if (!props.datasetName) return
  loading.value = true
  try {
    const res = await listAdminDatasetSemanticLinks(props.datasetName, {
      datasource_group_id: props.datasourceGroupId || undefined
    })
    if (res.status === 'success') {
      links.value = res.data?.links || []
    } else {
      ElMessage.error(res.msg || '获取语义模型关联失败')
    }
  } catch (err) {
    console.error('获取语义模型关联失败:', err)
  } finally {
    loading.value = false
  }
}

const loadAll = async () => {
  if (!props.modelValue || !props.datasetName) return
  await Promise.all([loadDataset(), loadLinks()])
}

watch(
  () => [props.modelValue, props.datasetName],
  () => {
    if (props.modelValue) void loadAll()
  }
)

watch(
  () => form.value.datasourceGroupId,
  () => {
    if (!formVisible.value || isEdit.value) return
    if (!groupOptions.value.includes(form.value.datasourceGroupId)) return
    form.value.semanticConfigId = ''
    form.value.metricScope = []
    if (!tableOptions.value.includes(form.value.tableName)) {
      form.value.tableName = tableOptions.value[0] || ''
    }
    void loadConfigs()
  }
)

watch(
  () => form.value.semanticConfigId,
  () => {
    form.value.metricScope = metricOptions.value
    if (!form.value.tableName) form.value.tableName = tableOptions.value[0] || ''
  }
)

const openCreate = async () => {
  isEdit.value = false
  resetForm()
  formVisible.value = true
  await loadConfigs()
}

const openEdit = async (link: DatasetSemanticLinkItem) => {
  isEdit.value = true
  form.value = {
    datasourceGroupId: link.datasource_group_id,
    semanticConfigId: link.semantic_config_id,
    tableName: link.table_name,
    metricScope: [...(link.metric_scope || [])],
    columnMappingsText: JSON.stringify(link.column_mappings || {}, null, 2),
    lifecycleStatus: link.lifecycle_status === 'active' ? 'active' : 'draft'
  }
  formVisible.value = true
  await loadConfigs()
  form.value.metricScope = (link.metric_scope || []).length ? [...(link.metric_scope || [])] : metricOptions.value
}

const submit = async () => {
  if (!form.value.semanticConfigId) {
    ElMessage.warning('请选择语义模型')
    return
  }
  if (!form.value.tableName) {
    ElMessage.warning('请选择/填写语义表名')
    return
  }
  let columnMappings: Record<string, string> = {}
  const rawText = form.value.columnMappingsText.trim()
  if (rawText) {
    try {
      const parsed = JSON.parse(rawText)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('结构必须是对象')
      columnMappings = parsed as Record<string, string>
    } catch (err) {
      ElMessage.error('列映射必须是 JSON 对象，例如 {"user_id": "用户ID"}')
      return
    }
  }
  saving.value = true
  try {
    const res = await upsertAdminDatasetSemanticLink(props.datasetName, form.value.semanticConfigId, {
      datasource_group_id: form.value.datasourceGroupId,
      table_name: form.value.tableName,
      column_mappings: columnMappings,
      metric_scope: form.value.metricScope,
      lifecycle_status: form.value.lifecycleStatus
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '保存语义模型关联失败')
      return
    }
    ElMessage.success('关联已保存')
    formVisible.value = false
    await loadLinks()
    emit('changed')
  } catch (err) {
    // 拦截器已提示具体错误（列/维度/指标校验失败等）
    console.error('保存语义模型关联失败:', err)
  } finally {
    saving.value = false
  }
}

const remove = async (link: DatasetSemanticLinkItem) => {
  try {
    await ElMessageBox.confirm(
      `确定删除 Dataset「${props.datasetName}」与语义模型「${link.semantic_config_name || link.semantic_config_id}」的关联？删除后对象类型将无法按该模型指标查询。`,
      '删除语义模型关联',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminDatasetSemanticLink(props.datasetName, link.semantic_config_id, {
      datasource_group_id: link.datasource_group_id
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '删除失败')
      return
    }
    ElMessage.success('已删除')
    await loadLinks()
    emit('changed')
  } catch (err) {
    console.error('删除语义模型关联失败:', err)
  }
}
</script>

<template>
  <el-dialog v-model="visible" title="Dataset ↔ 语义模型关联" width="min(980px, 94vw)" append-to-body>
    <div class="mb-2 flex items-center gap-2 text-xs text-slate-500">
      <i class="fa-solid fa-circle-info text-[10px] text-blue-400"></i>
      <span>
        Dataset：<span class="font-medium text-slate-700">{{ datasetName || '-' }}</span>
        · 只存引用与列对照，指标口径仍由语义层唯一定义
      </span>
      <el-button class="ml-auto" size="small" type="primary" @click="openCreate">
        <i class="fa-solid fa-plus mr-1 text-[10px]"></i>新建关联
      </el-button>
    </div>

    <el-table v-loading="loading" :data="links" size="small" border empty-text="尚未关联语义模型">
      <el-table-column prop="semantic_config_name" label="语义模型" min-width="150">
        <template #default="{ row }">
          <div class="text-xs text-slate-700">{{ row.semantic_config_name || row.semantic_config_id }}</div>
          <div class="font-mono text-[10px] text-slate-400">{{ row.semantic_config_id }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="datasource_group_id" label="数据源组" min-width="150" show-overflow-tooltip />
      <el-table-column prop="table_name" label="语义表" min-width="120" />
      <el-table-column label="指标范围" min-width="110">
        <template #default="{ row }">
          <span class="text-xs text-slate-600">{{ (row.metric_scope || []).length || '全部' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="statusMeta[row.lifecycle_status]?.tagType || 'info'" size="small" effect="plain">
            {{ statusMeta[row.lifecycle_status]?.label || row.lifecycle_status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" align="right">
        <template #default="{ row }">
          <el-button link size="small" @click="openEdit(row)">编辑</el-button>
          <el-button link size="small" type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="formVisible" :title="isEdit ? '编辑语义模型关联' : '新建语义模型关联'" width="min(620px, 92vw)" append-to-body>
      <el-form label-width="110px" label-position="left">
        <el-form-item label="数据源组" required>
          <el-select v-model="form.datasourceGroupId" size="small" class="w-full" :disabled="isEdit">
            <el-option v-for="group in groupOptions" :key="group" :label="group" :value="group" />
          </el-select>
        </el-form-item>
        <el-form-item label="语义模型" required>
          <el-select
            v-model="form.semanticConfigId"
            size="small"
            class="w-full"
            filterable
            :loading="configsLoading"
            :disabled="isEdit"
            placeholder="选择该数据源组下的语义模型"
          >
            <el-option
              v-for="config in configs"
              :key="config.id"
              :label="`${config.name}（${config.metrics.length} 个指标）`"
              :value="config.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="语义表名" required>
          <el-select v-model="form.tableName" size="small" class="w-full" filterable allow-create>
            <el-option v-for="table in tableOptions" :key="table" :label="table" :value="table" />
          </el-select>
        </el-form-item>
        <el-form-item label="指标范围">
          <el-select v-model="form.metricScope" size="small" class="w-full" multiple collapse-tags placeholder="空 = 该模型全部指标">
            <el-option v-for="name in metricOptions" :key="name" :label="name" :value="name" />
          </el-select>
        </el-form-item>
        <el-form-item label="列映射">
          <el-input
            v-model="form.columnMappingsText"
            type="textarea"
            :rows="4"
            class="font-mono"
            placeholder='JSON 对象：{"Dataset逻辑列": "语义维度名"}；留空 {} 表示按同名列自动对照'
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.lifecycleStatus" size="small">
            <el-radio-button value="draft">草稿</el-radio-button>
            <el-radio-button value="active">启用</el-radio-button>
          </el-radio-group>
          <span class="ml-2 text-[11px] text-slate-400">只有启用状态的关联可用于指标查询</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="formVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>
