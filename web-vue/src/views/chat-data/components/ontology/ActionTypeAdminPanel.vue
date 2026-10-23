<script setup lang="ts">
/**
 * 后台管理 · 本体 Action Type 面板。
 * 只覆盖定义/试运行/执行；执行写对象属性覆盖层 + 审计，不直接改源库。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AdminPanelToolbar from '../AdminPanelToolbar.vue'
import {
  deleteAdminOntologyActionType,
  executeAdminOntologyActionType,
  getAdminOntologyActionType,
  getAdminOntologyActionTypes,
  getAdminOntologyObjectType,
  getAdminOntologyObjectTypes,
  testRunAdminOntologyActionType,
  upsertAdminOntologyActionType
} from '../../api'
import type {
  OntologyActionTypeItem,
  OntologyActionTypePayload,
  OntologyActionTypeTestRunResult,
  OntologyObjectTypeItem,
  OntologyPropertyItem
} from '../../types'

const emit = defineEmits<{ changed: [] }>()

const loading = ref(false)
const hasLoaded = ref(false)
const items = ref<OntologyActionTypeItem[]>([])
const objectTypes = ref<OntologyObjectTypeItem[]>([])
const selectedObjectType = ref('')

const dialogVisible = ref(false)
const editing = ref(false)
const saving = ref(false)
const form = ref<ActionFormState>(createEmptyForm())
const selectedProperties = ref<OntologyPropertyItem[]>([])

const testVisible = ref(false)
const testing = ref(false)
const testTarget = ref<OntologyActionTypeItem | null>(null)
const testParameters = ref<Record<string, unknown>>({})
const testResult = ref<OntologyActionTypeTestRunResult | null>(null)
const testError = ref('')

const executeVisible = ref(false)
const executing = ref(false)
const executeTarget = ref<OntologyActionTypeItem | null>(null)
const executeObjectKey = ref('')
const executeParameters = ref<Record<string, unknown>>({})
const executeResult = ref<Record<string, unknown> | null>(null)
const executeError = ref('')

const STATUS_META = {
  draft: { label: '草稿', type: 'info' as const },
  experimental: { label: '实验', type: 'warning' as const },
  certified: { label: '认证', type: 'success' as const }
}
const statusMeta = (status: string) => STATUS_META[status as keyof typeof STATUS_META] || { label: status, type: 'info' as const }
const PARAMETER_TYPES = ['String', 'Integer', 'Decimal', 'Boolean', 'Date', 'DateTime'] as const

interface ParameterRow {
  name: string
  type: (typeof PARAMETER_TYPES)[number]
  required: boolean
  description: string
}

interface EditRow {
  property: string
  kind: 'parameter' | 'static'
  parameter: string
  staticValue: string
}

interface ActionFormState {
  name: string
  object_type_name: string
  display_name: string
  description: string
  status: 'draft' | 'experimental' | 'certified'
  parameters: ParameterRow[]
  edits: EditRow[]
}

function createEmptyParameter() {
  return { name: '', type: 'String' as const, required: true, description: '' }
}

function createEmptyEdit() {
  return { property: '', kind: 'parameter' as const, parameter: '', staticValue: '' }
}

function createEmptyForm(): ActionFormState {
  return {
    name: '',
    object_type_name: '',
    display_name: '',
    description: '',
    status: 'draft' as 'draft' | 'experimental' | 'certified',
    parameters: [createEmptyParameter()],
    edits: [createEmptyEdit()]
  }
}

const filteredItems = computed(() =>
  selectedObjectType.value ? items.value.filter(item => item.object_type_name === selectedObjectType.value) : items.value
)
const parameterNames = computed(() => form.value.parameters.map(item => item.name).filter(Boolean))

const loadList = async () => {
  loading.value = true
  try {
    const [actionRes, objectRes] = await Promise.all([getAdminOntologyActionTypes(), getAdminOntologyObjectTypes()])
    if (actionRes.status === 'success') {
      items.value = actionRes.data.action_types || []
      hasLoaded.value = true
    } else {
      ElMessage.error(actionRes.msg || '获取 Action Type 失败')
    }
    if (objectRes.status === 'success') objectTypes.value = objectRes.data.object_types || []
  } catch (err) {
    console.error('加载 Action Type 失败:', err)
    ElMessage.error('加载 Action Type 失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadList()
}

const loadProperties = async (objectTypeName: string) => {
  selectedProperties.value = []
  if (!objectTypeName) return
  try {
    const res = await getAdminOntologyObjectType(objectTypeName)
    if (res.status === 'success') selectedProperties.value = res.data.properties || []
  } catch (err) {
    console.error('加载对象属性失败:', err)
  }
}

const openCreate = async () => {
  editing.value = false
  form.value = createEmptyForm()
  dialogVisible.value = true
}

const openEdit = async (row: OntologyActionTypeItem) => {
  const res = await getAdminOntologyActionType(row.action_type_name)
  if (res.status !== 'success') {
    ElMessage.error(res.msg || '获取 Action Type 详情失败')
    return
  }
  const item = res.data
  editing.value = true
  form.value = {
    name: item.action_type_name,
    object_type_name: item.object_type_name,
    display_name: item.display_name,
    description: item.description,
    status: item.status,
    parameters: (item.parameters || []).map(param => ({ ...param })),
    edits: (item.edits || []).map(edit => ({
      property: edit.property,
      kind: edit.value?.kind || 'parameter',
      parameter: edit.value?.parameter || '',
      staticValue: edit.value?.value == null ? '' : String(edit.value.value)
    }))
  }
  await loadProperties(item.object_type_name)
  dialogVisible.value = true
}

const onObjectTypeChange = async (value: string) => {
  form.value.edits = [createEmptyEdit()]
  await loadProperties(value)
}

const addParameter = () => form.value.parameters.push(createEmptyParameter())
const removeParameter = (index: number) => form.value.parameters.splice(index, 1)
const addEdit = () => form.value.edits.push(createEmptyEdit())
const removeEdit = (index: number) => form.value.edits.splice(index, 1)

const buildPayload = (): OntologyActionTypePayload => ({
  object_type_name: form.value.object_type_name,
  display_name: form.value.display_name || form.value.name,
  description: form.value.description,
  status: form.value.status,
  parameters: form.value.parameters.map(param => ({
    name: param.name.trim(),
    type: param.type,
    required: Boolean(param.required),
    description: param.description || ''
  })),
  edits: form.value.edits.map(edit => ({
    operation: 'set_property',
    property: edit.property,
    value: edit.kind === 'parameter'
      ? { kind: 'parameter', parameter: edit.parameter }
      : { kind: 'static', value: edit.staticValue }
  })),
  allow_overwrite: editing.value
})

const save = async () => {
  if (!/^[A-Za-z0-9_]{1,128}$/.test(form.value.name)) {
    ElMessage.warning('Action Type API 名仅允许字母、数字、下划线，长度 1-128')
    return
  }
  if (!form.value.object_type_name) {
    ElMessage.warning('请选择目标对象类型')
    return
  }
  saving.value = true
  try {
    const res = await upsertAdminOntologyActionType(form.value.name, buildPayload())
    if (res.status === 'success') {
      ElMessage.success('保存成功')
      dialogVisible.value = false
      await loadList()
      emit('changed')
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    console.error('保存 Action Type 失败:', err)
    ElMessage.error('保存失败，请检查参数与编辑定义')
  } finally {
    saving.value = false
  }
}

const remove = async (row: OntologyActionTypeItem) => {
  try {
    await ElMessageBox.confirm(`确定删除 Action Type ${row.action_type_name}？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  const res = await deleteAdminOntologyActionType(row.action_type_name)
  if (res.status === 'success') {
    ElMessage.success('已删除')
    await loadList()
    emit('changed')
  } else {
    ElMessage.error(res.msg || '删除失败')
  }
}

const openTest = (row: OntologyActionTypeItem) => {
  testTarget.value = row
  testParameters.value = Object.fromEntries((row.parameters || []).map(param => [param.name, '']))
  testResult.value = null
  testError.value = ''
  testVisible.value = true
}

const runTest = async () => {
  if (!testTarget.value) return
  testing.value = true
  testError.value = ''
  try {
    const res = await testRunAdminOntologyActionType(testTarget.value.action_type_name, testParameters.value)
    if (res.status === 'success') testResult.value = res.data
    else testError.value = res.msg || '试运行失败'
  } catch (err) {
    console.error('试运行失败:', err)
    testError.value = '试运行失败，请检查参数'
  } finally {
    testing.value = false
  }
}

const openExecute = (row: OntologyActionTypeItem) => {
  executeTarget.value = row
  executeObjectKey.value = ''
  executeParameters.value = Object.fromEntries((row.parameters || []).map(param => [param.name, '']))
  executeResult.value = null
  executeError.value = ''
  executeVisible.value = true
}

const runExecute = async () => {
  if (!executeTarget.value || !executeObjectKey.value.trim()) {
    executeError.value = '请输入对象主键'
    return
  }
  executing.value = true
  executeError.value = ''
  try {
    const res = await executeAdminOntologyActionType(
      executeTarget.value.action_type_name,
      executeObjectKey.value.trim(),
      executeParameters.value
    )
    if (res.status === 'success') {
      executeResult.value = res.data
      emit('changed')
    } else executeError.value = res.msg || '执行失败'
  } catch (err) {
    console.error('执行 Action 失败:', err)
    executeError.value = '执行失败，请检查参数、对象或执行开关'
  } finally {
    executing.value = false
  }
}

defineExpose({ ensureLoaded, openCreate, openEdit, openTest, openExecute, remove, loadList })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <AdminPanelToolbar>
      <template #actions>
        <el-button size="small" type="primary" @click="openCreate">
          <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>新建 Action Type
        </el-button>
        <el-button size="small" :loading="loading" @click="loadList">
          <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
        </el-button>
      </template>
    </AdminPanelToolbar>

    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <el-table :data="filteredItems" v-loading="loading" size="small" height="100%" empty-text="暂无 Action Type">
        <el-table-column prop="action_type_name" label="动作类型" min-width="180" show-overflow-tooltip />
        <el-table-column prop="object_type_name" label="目标对象类型" min-width="150" show-overflow-tooltip />
        <el-table-column label="参数" width="70" align="center">
          <template #default="{ row }">{{ (row.parameters || []).length }}</template>
        </el-table-column>
        <el-table-column label="编辑" width="70" align="center">
          <template #default="{ row }">{{ (row.edits || []).length }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusMeta(row.status).type" effect="plain">
              {{ statusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }"><span class="text-xs text-slate-500">{{ row.create_time || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link size="small" @click="openTest(row)">试运行</el-button>
            <el-button link size="small" type="warning" @click="openExecute(row)">执行</el-button>
            <el-button link size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑 Action Type' : '新建 Action Type'" width="900px" destroy-on-close>
      <el-form label-width="110px" label-position="left">
        <el-form-item label="API 名" required>
          <el-input v-model="form.name" :disabled="editing" placeholder="字母/数字/下划线，如 update_order_status" />
        </el-form-item>
        <el-form-item label="目标对象类型" required>
          <el-select v-model="form.object_type_name" filterable class="w-full" @change="onObjectTypeChange">
            <el-option v-for="item in objectTypes" :key="item.object_type_name" :label="`${item.object_type_name}（${item.display_name || item.object_type_name}）`" :value="item.object_type_name" />
          </el-select>
        </el-form-item>
        <el-form-item label="展示名">
          <el-input v-model="form.display_name" placeholder="面向用户展示的动作名" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="状态">
          <el-radio-group v-model="form.status">
            <el-radio-button value="draft">草稿</el-radio-button>
            <el-radio-button value="experimental">实验</el-radio-button>
            <el-radio-button value="certified">认证</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="参数">
          <div class="w-full">
            <div v-for="(param, index) in form.parameters" :key="index" class="mb-2 flex items-center gap-2">
              <el-input v-model="param.name" size="small" placeholder="参数名" class="!w-36" />
              <el-select v-model="param.type" size="small" class="!w-32">
                <el-option v-for="type in PARAMETER_TYPES" :key="type" :label="type" :value="type" />
              </el-select>
              <el-checkbox v-model="param.required">必填</el-checkbox>
              <el-input v-model="param.description" size="small" placeholder="说明" />
              <el-button link type="danger" @click="removeParameter(index)">删除</el-button>
            </div>
            <el-button link type="primary" @click="addParameter">+ 添加参数</el-button>
          </div>
        </el-form-item>
        <el-form-item label="编辑">
          <div class="w-full">
            <div v-for="(edit, index) in form.edits" :key="index" class="mb-2 flex items-center gap-2">
              <el-select v-model="edit.property" size="small" placeholder="属性" class="!w-44" filterable>
                <el-option v-for="prop in selectedProperties.filter(item => !item.is_identifier)" :key="prop.property_name" :label="prop.property_name" :value="prop.property_name" />
              </el-select>
              <span class="text-xs text-slate-500">=</span>
              <el-select v-model="edit.kind" size="small" class="!w-24">
                <el-option label="参数" value="parameter" />
                <el-option label="静态值" value="static" />
              </el-select>
              <el-select v-if="edit.kind === 'parameter'" v-model="edit.parameter" size="small" class="!w-36">
                <el-option v-for="name in parameterNames" :key="name" :label="name" :value="name" />
              </el-select>
              <el-input v-else v-model="edit.staticValue" size="small" placeholder="静态值" class="!w-36" />
              <el-button link type="danger" @click="removeEdit(index)">删除</el-button>
            </div>
            <el-button link type="primary" @click="addEdit">+ 添加编辑</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="testVisible" title="Action Type 试运行" width="640px" destroy-on-close>
      <p class="mb-3 text-xs text-slate-500">{{ testTarget?.action_type_name }}（只校验参数，不写对象）</p>
      <div v-for="param in testTarget?.parameters || []" :key="param.name" class="mb-2 flex items-center gap-2">
        <span class="w-32 text-xs text-slate-600">{{ param.name }}<span v-if="param.required" class="text-red-500">*</span></span>
        <el-input v-model="testParameters[param.name]" size="small" :placeholder="param.type" />
      </div>
      <el-alert v-if="testError" :title="testError" type="error" :closable="false" show-icon />
      <pre v-if="testResult" class="mt-2 max-h-300 overflow-auto rounded bg-slate-50 p-3 text-xs">{{ JSON.stringify(testResult, null, 2) }}</pre>
      <template #footer>
        <el-button @click="testVisible = false">关闭</el-button>
        <el-button type="primary" :loading="testing" @click="runTest">试运行</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="executeVisible" title="执行 Action Type" width="640px" destroy-on-close>
      <p class="mb-3 text-xs text-slate-500">{{ executeTarget?.action_type_name }}（写对象覆盖层 + 审计）</p>
      <div class="mb-2 flex items-center gap-2">
        <span class="w-32 text-xs text-slate-600">对象主键</span>
        <el-input v-model="executeObjectKey" size="small" placeholder="请输入对象主键" />
      </div>
      <div v-for="param in executeTarget?.parameters || []" :key="param.name" class="mb-2 flex items-center gap-2">
        <span class="w-32 text-xs text-slate-600">{{ param.name }}<span v-if="param.required" class="text-red-500">*</span></span>
        <el-input v-model="executeParameters[param.name]" size="small" :placeholder="param.type" />
      </div>
      <el-alert v-if="executeError" :title="executeError" type="error" :closable="false" show-icon />
      <pre v-if="executeResult" class="mt-2 max-h-300 overflow-auto rounded bg-slate-50 p-3 text-xs">{{ JSON.stringify(executeResult, null, 2) }}</pre>
      <template #footer>
        <el-button @click="executeVisible = false">关闭</el-button>
        <el-button type="warning" :loading="executing" @click="runExecute">执行</el-button>
      </template>
    </el-dialog>
  </div>
</template>
