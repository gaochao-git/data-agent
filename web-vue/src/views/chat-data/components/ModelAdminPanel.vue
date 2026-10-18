<script setup lang="ts">
/**
 * 后台管理 · 模型注册表面板。
 * 智能问数可用模型清单的唯一维护入口（DB 为真值，变更即时刷新进程内缓存，
 * 多实例最迟一个 TTL 生效）。模型参数集中在 params 对象（temperature/
 * context_window/max_output_tokens/vlm/thinking），api_key 只写不读（掩码）。
 * 连接测试用表单当前值发最小请求，不要求先保存。
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createAdminModel,
  deleteAdminModel,
  discoverAdminModels,
  getAdminModels,
  setAdminModelDefault,
  setAdminModelEnabled,
  testAdminModelConnection,
  updateAdminModel
} from '../api'
import type { ChatDataModelRegistryItem, ChatDataModelThinking } from '../types'

// 档位候选建议（allow-create 下拉的快捷项；档位名实际自由，受后端字符集/数量约束）；
// off/none 选中时后端按「关」形态序列化，其余对齐 DeepSeek 新 API 档位族
const EFFORT_SUGGESTIONS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'ultra', 'max'] as const
const THINKING_DIALECTS = [
  { value: 'chat_template', label: 'chat_template（纯布尔）' },
  { value: 'reasoning_effort', label: 'reasoning_effort（档位）' },
  { value: 'openrouter', label: 'openrouter' }
] as const

const loading = ref(false)
const items = ref<ChatDataModelRegistryItem[]>([])
const hasLoaded = ref(false)

const loadModels = async () => {
  loading.value = true
  try {
    const res = await getAdminModels()
    if (res.status === 'success') {
      items.value = res.data.models || []
      hasLoaded.value = true
    } else {
      ElMessage.error(res.msg || '获取模型列表失败')
    }
  } catch (err) {
    console.error('获取模型列表失败:', err)
    ElMessage.error('获取模型列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadModels()
}

// ----------- 编辑弹窗 -----------

const dialogVisible = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref<{ success: boolean; text: string } | null>(null)
/** 编辑时为原 model_key（注册键，不可改，路径参数身份） */
const editingModelId = ref('')
const isCreating = computed(() => !editingModelId.value)

const form = reactive({
  model_id: '',
  provider: '',
  display_name: '',
  base_url: '',
  api_key: '',
  enabled: true,
  remark: '',
  temperature: 0.2,
  context_window: 48000,
  max_output_tokens: null as number | null,
  vlm: true,
  thinking_mode: 'none' as ChatDataModelThinking['mode'],
  thinking_dialect: 'reasoning_effort',
  // 档位行编辑器：value 为 UI 字符串（空=与档位同名）；保存时按规则解析为 string/bool/number
  thinking_efforts: [] as Array<{ level: string; value: string }>,
  thinking_default_effort: ''
})

// chat_template 为纯布尔协议不支持档位；default_effort 必须在档位列表内
const effortsAvailable = computed(() => form.thinking_mode !== 'none' && form.thinking_dialect !== 'chat_template')

// 参数值解析：空=同名省略；true/false→布尔；纯数字→number；其余按字符串（后端同规则校验）
const parseWireValue = (raw: string): string | number | boolean | undefined => {
  if (raw === '') return undefined
  if (raw === 'true') return true
  if (raw === 'false') return false
  if (!Number.isNaN(Number(raw))) return Number(raw)
  return raw
}

const addEffortRow = () => {
  form.thinking_efforts.push({ level: '', value: '' })
}

const removeEffortRow = (index: number) => {
  const [removed] = form.thinking_efforts.splice(index, 1)
  // 默认档位联动：被删档位是当前默认时清空选择
  if (removed && removed.level.trim() === form.thinking_default_effort) {
    form.thinking_default_effort = ''
  }
}

// 行内勾选=默认档位（单选语义：勾选即替换，取消勾选清空）
const setDefaultEffortRow = (row: { level: string; value: string }, checked: boolean) => {
  const level = row.level.trim()
  if (checked) {
    form.thinking_default_effort = level
  } else if (form.thinking_default_effort === level) {
    form.thinking_default_effort = ''
  }
}

// 单次最大输出必填；未手动定制时随上下文窗口联动（默认取 1/4）
const maxOutputTouched = ref(false)
const quarterOf = (contextWindow: number) => Math.max(1, Math.floor(contextWindow / 4))
watch(
  () => form.context_window,
  contextWindow => {
    if (!maxOutputTouched.value) form.max_output_tokens = quarterOf(contextWindow)
  }
)

// 注册键由服务端自动生成，表单不出现；提供商由用户填写（与请求名组成业务唯一键）

const resetForm = () => {
  form.model_id = ''
  form.provider = ''
  form.display_name = ''
  form.base_url = ''
  form.api_key = ''
  form.enabled = true
  form.remark = ''
  form.temperature = 0.2
  form.context_window = 48000
  maxOutputTouched.value = false
  form.max_output_tokens = quarterOf(48000)
  form.vlm = true
  form.thinking_mode = 'none'
  form.thinking_dialect = 'reasoning_effort'
  form.thinking_efforts = []
  form.thinking_default_effort = ''
  testResult.value = null
  modelIdOptions.value = []
}

// 自动探测：用表单 Base URL + 密文拉取上游 /models 清单，请求名可从结果中选（也可手输）
const modelIdOptions = ref<string[]>([])
const discovering = ref(false)
const discoverModels = async () => {
  if (!form.base_url.trim()) {
    ElMessage.warning('请先填写 Base URL')
    return
  }
  if (!form.api_key.trim()) {
    ElMessage.warning('请先填写 API Key（密文）')
    return
  }
  discovering.value = true
  try {
    const res = await discoverAdminModels({ base_url: form.base_url.trim(), api_key: form.api_key.trim() })
    if (res.status === 'success') {
      modelIdOptions.value = res.data.models || []
      if (modelIdOptions.value.length) {
        ElMessage.success(`探测到 ${modelIdOptions.value.length} 个模型，点击请求名输入框可直接选择`)
      } else {
        ElMessage.warning('上游未返回任何模型')
      }
    } else {
      ElMessage.error(res.msg || '模型探测失败')
    }
  } catch (err) {
    console.error('模型探测失败:', err)
    ElMessage.error('模型探测失败，请稍后重试')
  } finally {
    discovering.value = false
  }
}

const openCreate = () => {
  editingModelId.value = ''
  resetForm()
  dialogVisible.value = true
}

const fillForm = (row: ChatDataModelRegistryItem) => {
  form.model_id = row.model_id
  form.provider = row.provider || ''
  form.display_name = row.display_name
  form.base_url = row.base_url
  form.api_key = row.api_key_cipher || '' // 密文回填：编辑框直接带回已存密文
  form.enabled = row.enabled
  form.remark = row.remark_text
  const params = row.params || {}
  form.temperature = params.temperature ?? 0.2
  form.context_window = params.context_window ?? 48000
  if (params.max_output_tokens != null) {
    form.max_output_tokens = params.max_output_tokens
    maxOutputTouched.value = true
  } else {
    maxOutputTouched.value = false
    form.max_output_tokens = quarterOf(form.context_window)
  }
  form.vlm = params.vlm ?? true
  const thinking = params.thinking
  form.thinking_mode = thinking?.mode ?? 'none'
  form.thinking_dialect = thinking?.dialect || 'reasoning_effort'
  // 兼容旧 string[]（存量数据）与新 [{level, value}] 两种形状，统一转为行编辑器
  form.thinking_efforts = (thinking?.efforts || []).map(item =>
    typeof item === 'string'
      ? { level: item, value: '' }
      : { level: item.level, value: item.value == null ? '' : String(item.value) }
  )
  form.thinking_default_effort = thinking?.default_effort || ''
}

const openEdit = (row: ChatDataModelRegistryItem) => {
  editingModelId.value = row.model_key
  resetForm()
  fillForm(row)
  dialogVisible.value = true
}

/** 复制模型：以该行为模板预填表单，走新建流程（请求名/提供商可改，密文直接带回）。 */
const openDuplicate = (row: ChatDataModelRegistryItem) => {
  editingModelId.value = ''
  resetForm()
  fillForm(row)
  dialogVisible.value = true
}

const buildRequestParams = () => {
  const params: ChatDataModelRegistryItem['params'] = {
    temperature: form.temperature,
    context_window: form.context_window,
    max_output_tokens: form.max_output_tokens,
    vlm: form.vlm
  }
  if (form.thinking_mode === 'none') {
    // mode=none 只发 mode 本身（后端校验禁止 none 携带其它字段）
    params.thinking = { mode: 'none' }
  } else {
    const thinking: NonNullable<ChatDataModelRegistryItem['params']['thinking']> = {
      mode: form.thinking_mode,
      dialect: form.thinking_dialect
    }
    // 默认开启由后端按默认档位推导；无档位方言（chat_template）不发 efforts
    if (effortsAvailable.value) {
      if (form.thinking_efforts.length > 0) {
        // 新形状 [{level, value?}]：value 空 = 与档位同名（省略键）
        thinking.efforts = form.thinking_efforts.map(row => {
          const level = row.level.trim()
          const value = parseWireValue(row.value.trim())
          return value === undefined ? { level } : { level, value }
        })
        if (form.thinking_default_effort) thinking.default_effort = form.thinking_default_effort
      }
    }
    params.thinking = thinking
  }
  return params
}

const validateForm = (): string | null => {
  if (!form.model_id.trim()) return '请填写模型请求名'
  if (/\s/.test(form.model_id.trim())) return '模型请求名不能包含空白字符'
  if (!form.base_url.trim()) return '请填写 Base URL（连接信息只以模型行为准）'
  if (!/^https?:\/\//.test(form.base_url.trim())) return 'Base URL 必须以 http:// 或 https:// 开头'
  if (!form.api_key.trim()) return '请填写 API Key（密文）'
  // 只接受密文形态（base64:base64），明文拒绝；文案不暴露算法与生成方式
  if (!/^[A-Za-z0-9+/]{8,}={0,2}:[A-Za-z0-9+/]{8,}={0,2}$/.test(form.api_key.trim())) {
    return 'API Key 必须为加密后的密文（形如 xxx:yyy）'
  }
  if (!(form.context_window > 0)) return '上下文窗口应为正整数'
  if (form.max_output_tokens === null) return '请填写单次最大输出（默认为上下文窗口的 1/4）'
  if (form.max_output_tokens >= form.context_window) {
    return '单次最大输出必须小于上下文窗口'
  }
  if (form.thinking_mode === 'optional' && !effortsAvailable.value) return '' // optional 无档位合法
  // 档位行编辑器校验（与后端 _parse_thinking 同规则）
  if (effortsAvailable.value) {
    if (form.thinking_efforts.length > 10) return '档位数量超过上限 10'
    const seen = new Set<string>()
    for (const row of form.thinking_efforts) {
      const level = row.level.trim()
      if (!level) return '档位名不能为空'
      if (!/^[A-Za-z0-9_.:-]{1,32}$/.test(level)) return `档位名 ${level} 非法（限 1~32 位字母/数字/_.:-）`
      if (seen.has(level)) return `档位名重复: ${level}`
      seen.add(level)
      if (row.value.trim().length > 64) return '档位参数值字符串超长（≤64）'
    }
    if (form.thinking_default_effort && !seen.has(form.thinking_default_effort)) return '默认档位必须在档位列表中'
  }
  return null
}

const saveModel = async () => {
  const error = validateForm()
  if (error) {
    ElMessage.warning(error)
    return
  }
  saving.value = true
  try {
    const body = {
      model_id: form.model_id.trim(),
      provider: form.provider.trim(),
      display_name: form.display_name.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      params: buildRequestParams(),
      enabled: form.enabled,
      remark: form.remark.trim()
    }
    const res = isCreating.value ? await createAdminModel(body) : await updateAdminModel(editingModelId.value, body)
    if (res.status === 'success') {
      ElMessage.success(isCreating.value ? '模型已新增' : '模型已更新')
      dialogVisible.value = false
      await loadModels()
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    console.error('保存模型失败:', err)
    ElMessage.error('保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

const runConnectionTest = async () => {
  const error = validateForm()
  if (error) {
    ElMessage.warning(error)
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const res = await testAdminModelConnection({
      model_id: form.model_id.trim(),
      base_url: form.base_url.trim(),
      api_key: form.api_key.trim(),
      params: buildRequestParams()
    })
    if (res.status === 'success') {
      const data = res.data
      if (data.success) {
        testResult.value = { success: true, text: `连通正常（${data.latency_ms}ms）：${data.sample || '-'}` }
      } else {
        testResult.value = { success: false, text: `连通失败（${data.latency_ms}ms）：${data.error || '未知错误'}` }
      }
    } else {
      testResult.value = { success: false, text: res.msg || '连接测试执行失败' }
    }
  } catch (err) {
    console.error('连接测试失败:', err)
    testResult.value = { success: false, text: '连接测试执行失败，请稍后重试' }
  } finally {
    testing.value = false
  }
}

// ----------- 行操作 -----------

const toggleEnabled = async (row: ChatDataModelRegistryItem) => {
  try {
    const res = await setAdminModelEnabled(row.model_key, !row.enabled)
    if (res.status === 'success') {
      ElMessage.success(!row.enabled ? '已启用' : '已停用')
      await loadModels()
    } else {
      ElMessage.error(res.msg || '启停失败')
    }
  } catch (err) {
    console.error('启停模型失败:', err)
    ElMessage.error('启停失败，请稍后重试')
  }
}

const markDefault = async (row: ChatDataModelRegistryItem) => {
  try {
    const res = await setAdminModelDefault(row.model_key)
    if (res.status === 'success') {
      ElMessage.success(`已将「${row.model_id}」设为默认模型`)
      await loadModels()
    } else {
      ElMessage.error(res.msg || '设置默认失败')
    }
  } catch (err) {
    console.error('设置默认模型失败:', err)
    ElMessage.error('设置默认失败，请稍后重试')
  }
}

const removeModel = async (row: ChatDataModelRegistryItem) => {
  try {
    await ElMessageBox.confirm(
      `删除后「${row.display_name || row.model_id}」立即从可用清单移除（重新添加同供应商同请求名将生成新行）。确定删除？`,
      '删除模型',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminModel(row.model_key)
    if (res.status === 'success') {
      ElMessage.success('已删除')
      await loadModels()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    console.error('删除模型失败:', err)
    ElMessage.error('删除失败，请稍后重试')
  }
}

const formatThinking = (row: ChatDataModelRegistryItem) => {
  const thinking = row.params?.thinking
  if (!thinking || thinking.mode === 'none') return '不支持'
  const dialect = thinking.dialect || '-'
  if (thinking.mode === 'forced') return `强制 / ${dialect}`
  // 默认开启由默认档位推导：默认档位为普通档=默认开；off/none 或未勾=默认关
  const defaultOn = !!thinking.default_effort && !['off', 'none'].includes(thinking.default_effort)
  return `可选 / ${dialect}${defaultOn ? '（默认开）' : '（默认关）'}`
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 工具栏 -->
    <div class="flex shrink-0 items-center gap-2 border-b border-slate-100 px-4 py-2">
      <el-button size="small" :loading="loading" @click="loadModels">
        <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
      </el-button>
      <div class="flex-1"></div>
      <el-button size="small" type="primary" round @click="openCreate">
        <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
        新增模型
      </el-button>
    </div>

    <!-- 模型列表 -->
    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <el-table
        :data="items"
        v-loading="loading"
        size="small"
        height="100%"
        style="width: 100%"
        empty-text="暂无模型（可点击右上角新增，或放置 CHAT_DATA_MODELS_FILE 由系统自动导入）"
      >
        <el-table-column label="默认" width="60" align="center">
          <template #default="{ row }">
            <span
              v-if="row.is_default"
              class="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-blue-500"
              title="当前默认模型"
            >
              <span class="h-1.5 w-1.5 rounded-full bg-blue-500"></span>
            </span>
            <span
              v-else
              class="inline-flex h-3.5 w-3.5 cursor-pointer rounded-full border border-slate-300 transition-colors hover:border-blue-400"
              title="设为默认模型"
              @click="markDefault(row)"
            ></span>
          </template>
        </el-table-column>
        <el-table-column label="模型请求名" min-width="200">
          <template #default="{ row }">
            <span class="font-mono text-xs text-slate-800">{{ row.model_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="供应商" min-width="110">
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.provider || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="显示名" min-width="140">
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.display_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Base URL" min-width="200">
          <template #default="{ row }">
            <span class="font-mono text-xs break-all text-slate-500">{{ row.base_url || '（未配置）' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="温度" width="60" align="center">
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.params?.temperature ?? '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="上下文" width="80" align="center">
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.params?.context_window ?? '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="思考能力" min-width="150">
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ formatThinking(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Key" width="60" align="center">
          <template #default="{ row }">
            <i :class="['fa-solid text-[11px]', row.has_api_key ? 'fa-key text-emerald-500' : 'fa-circle-minus text-slate-300']"></i>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <span
              :class="['inline-flex h-5 items-center rounded-full px-2 text-[11px]', row.enabled ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-500']"
            >
              {{ row.enabled ? '启用' : '停用' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="最近修改" min-width="150">
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.update_time ? `${row.update_time} ${row.update_by_name || ''}` : '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right" class-name="model-op-col">
          <template #default="{ row }">
            <el-button link size="small" @click="openEdit(row)">
              <i class="fa-solid fa-pen text-[11px]"></i> 编辑
            </el-button>
            <el-button link size="small" type="primary" @click="openDuplicate(row)">
              <i class="fa-regular fa-copy text-[11px]"></i> 复制
            </el-button>
            <el-button link size="small" :type="row.enabled ? 'warning' : 'success'" @click="toggleEnabled(row)">
              {{ row.enabled ? '停用' : '启用' }}
            </el-button>
            <el-button link size="small" type="danger" @click="removeModel(row)">
              <i class="fa-solid fa-trash text-[11px]"></i> 删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialogVisible" width="min(640px, 94vw)" :append-to-body="true">
      <template #header>
        <span class="text-base font-medium text-slate-800">{{ isCreating ? '新增模型' : `编辑模型：${form.model_id}` }}</span>
      </template>
      <div class="flex flex-col gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">Base URL（必填，连接信息只以模型行为准）</div>
          <el-input v-model="form.base_url" size="small" placeholder="http://127.0.0.1:8000/v1" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">API Key（密文，必填，编辑时自动回填）</div>
          <el-input
            v-model="form.api_key"
            size="small"
            type="password"
            show-password
            :placeholder="isCreating ? '请粘贴加密后的 API Key 密文' : '已回填保存的密文，可修改'"
          />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-xs text-slate-500">模型请求名 <span class="text-red-500">*</span></span>
              <el-button
                link
                type="primary"
                size="small"
                :loading="discovering"
                title="用上方 Base URL 和 API Key 拉取上游可用模型清单"
                @click="discoverModels"
              >
                自动探测
              </el-button>
            </div>
            <el-select
              v-if="modelIdOptions.length > 0"
              v-model="form.model_id"
              size="small"
              filterable
              allow-create
              default-first-option
              placeholder="选择探测到的模型，或直接输入"
            >
              <el-option v-for="item in modelIdOptions" :key="item" :label="item" :value="item" />
            </el-select>
            <el-input v-else v-model="form.model_id" size="small" placeholder="如 Qwen3.6-35B-A3B-4bit" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">提供商（选填，与请求名组成唯一组合）</div>
            <el-input v-model="form.provider" size="small" placeholder="如 DeepSeek / 阿里云 / 本地 vLLM" />
          </div>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">显示名（空则用请求名）</div>
          <el-input v-model="form.display_name" size="small" placeholder="展示用名称" />
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div>
            <div class="mb-1 text-xs text-slate-500">温度（0~2）</div>
            <el-input-number v-model="form.temperature" size="small" :min="0" :max="2" :step="0.1" :precision="2" class="!w-full" controls-position="right" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">上下文窗口（token）</div>
            <el-input-number v-model="form.context_window" size="small" :min="1" :step="1000" :precision="0" class="!w-full" controls-position="right" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">单次最大输出（token）</div>
            <el-input-number v-model="form.max_output_tokens" size="small" :min="1" :step="1000" :precision="0" class="!w-full" controls-position="right" @change="maxOutputTouched = true" />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="mb-1 text-xs text-slate-500">思考能力模式</div>
            <el-select v-model="form.thinking_mode" size="small" class="!w-full">
              <el-option label="不支持思考" value="none" />
              <el-option label="可选（用户可开关）" value="optional" />
              <el-option label="强制（始终开启）" value="forced" />
            </el-select>
          </div>
          <div v-if="form.thinking_mode !== 'none'">
            <div class="mb-1 text-xs text-slate-500">协议方言</div>
            <el-select v-model="form.thinking_dialect" size="small" class="!w-full">
              <el-option v-for="item in THINKING_DIALECTS" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </div>
        </div>
        <div v-if="form.thinking_mode !== 'none'" class="rounded-md border border-slate-100 bg-slate-50/60 px-3 py-2.5">
          <div v-if="effortsAvailable">
            <div class="mb-1 flex items-center gap-2 text-xs text-slate-500">
              <span>支持档位</span>
              <span class="text-slate-400">勾选＝默认档位（不勾则按方言兜底）</span>
            </div>
            <div class="space-y-1.5">
              <div v-for="(row, index) in form.thinking_efforts" :key="index" class="flex items-center gap-1.5">
                <el-checkbox
                  :model-value="row.level.trim() !== '' && row.level.trim() === form.thinking_default_effort"
                  :disabled="!row.level.trim()"
                  class="shrink-0"
                  title="勾选设为默认档位"
                  @change="setDefaultEffortRow(row, $event as boolean)"
                />
                <el-select
                  v-model="row.level"
                  size="small"
                  filterable
                  allow-create
                  default-first-option
                  class="!w-36 shrink-0"
                  placeholder="档位名"
                >
                  <el-option v-for="level in EFFORT_SUGGESTIONS" :key="level" :label="level" :value="level" />
                </el-select>
                <i class="fa-solid fa-arrow-right shrink-0 text-[10px] text-slate-300"></i>
                <el-input
                  v-model="row.value"
                  size="small"
                  class="min-w-0 flex-1"
                  placeholder="API 参数值（缺省与档位同名）"
                />
                <el-button link type="danger" size="small" class="shrink-0" title="删除该档位" @click="removeEffortRow(index)">
                  <i class="fa-solid fa-trash text-[11px]"></i>
                </el-button>
              </div>
              <el-button link type="primary" size="small" @click="addEffortRow">
                <i class="fa-solid fa-plus text-[10px]" style="margin-right: 4px"></i>添加档位
              </el-button>
            </div>
          </div>
          <div class="flex flex-wrap items-end gap-4">
            <div class="text-xs text-slate-400">默认开启与否由「默认档位」勾选推导：普通档=默认开；off/none 或不勾=默认关</div>
          </div>
        </div>
        <div class="grid grid-cols-2 items-center gap-3">
          <div class="flex items-center gap-2">
            <span class="text-xs text-slate-500">启用</span>
            <el-switch v-model="form.enabled" />
          </div>
          <div class="flex items-center gap-2">
            <span class="text-xs text-slate-500">视觉能力（vlm）</span>
            <el-switch v-model="form.vlm" />
          </div>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">备注</div>
          <el-input v-model="form.remark" size="small" maxlength="500" placeholder="选填" />
        </div>
        <div v-if="testResult" class="rounded-md px-3 py-2 text-xs" :class="testResult.success ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'">
          {{ testResult.text }}
        </div>
      </div>
      <template #footer>
        <div class="flex w-full items-center justify-between">
          <el-button size="small" :loading="testing" @click="runConnectionTest">
            <i class="fa-solid fa-plug-circle-bolt" style="margin-right: 4px; font-size: 10px;"></i>
            连接测试
          </el-button>
          <div class="flex gap-2">
            <el-button size="small" @click="dialogVisible = false">取消</el-button>
            <el-button type="primary" size="small" :loading="saving" @click="saveModel">保存</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 操作列四个按钮必须同行，不许折行 */
:deep(.model-op-col .cell) {
  white-space: nowrap;
}
</style>
