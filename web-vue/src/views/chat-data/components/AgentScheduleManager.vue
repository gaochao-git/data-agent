<script setup lang="ts">
/**
 * 用户侧定时任务管理弹窗。
 * 列出当前用户的智能体定时任务，支持新建/编辑/启停/删除与执行历史查看。
 * 任务到点后由后端调度器在绑定会话内自动执行一次智能问数。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createAgentSchedule,
  deleteAgentSchedule,
  getAgentScheduleRuns,
  getAgentSchedules,
  getChatDataConfig,
  getConversations,
  getDatasources,
  triggerAgentScheduleNow,
  updateAgentSchedule
} from '../api'
import type { AgentScheduleItem, AgentSchedulePayload, AgentScheduleRunItem, ChatDataModelOption, ConversationItem, DataSource } from '../types'
import { formatDateTimeValue } from '../utils'
import AgentScheduleRunsTable from './AgentScheduleRunsTable.vue'
import {
  WEEKLY_DAY_OPTIONS,
  buildRuntimeOptionsPatch,
  buildSchedulePatch,
  buildThinkingKeys,
  createScheduleBaseline,
  parseCronToPreset,
  schedulePresetPreview,
  scheduleText,
  weeklyDayShortLabel
} from '../utils/agentSchedule'
import type { ScheduleCronPreset, ScheduleFormBaseline } from '../utils/agentSchedule'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 新建任务时默认绑定的会话；为空时不允许创建 */
  conversationId: { type: String, default: '' },
  /** 当前会话绑定的数据源，创建任务时随运行选项带上 */
  datasourceId: { type: String, default: '' }
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  openConversation: [conversationId: string]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value)
})

const loading = ref(false)
const jobs = ref<AgentScheduleItem[]>([])
const hasLoaded = ref(false)

const editorVisible = ref(false)
const editingJobId = ref('')
const savingJob = ref(false)
const form = ref({
  title: '',
  prompt: '',
  conversationMode: 'fixed' as 'fixed' | 'new_each_run',
  conversationId: '',
  modelId: '',
  thinkingEffort: '',
  cronPreset: 'daily' as ScheduleCronPreset,
  hourlyMinute: 0,
  dailyTime: '09:00',
  weeklyDays: [1] as number[],
  monthlyDay: 1,
  cronExpr: '0 9 * * *',
  planTime: '' as string | Date,
  effectiveEndTime: ''
})

const runsVisible = ref(false)
const runsLoading = ref(false)
const runsJob = ref<AgentScheduleItem | null>(null)
const runs = ref<AgentScheduleRunItem[]>([])

// 编辑基线：打开弹窗时的任务快照，提交时据它生成只含改动项的补丁（见 utils/agentSchedule）
const editorBaseline = ref<ScheduleFormBaseline>(createScheduleBaseline())

// 编辑中的任务所绑会话的标题：会话不在「最近 50 条」里时用于下拉回显
const editingConversationTitle = ref('')

// 立即执行防连点：同一时刻只允许一个在途触发请求，避免堆积多个手动实例
const triggeringJobId = ref('')

// 数据源组下拉：创建时选定任务执行用的数据源（新会话模式会绑定到新建会话）
const datasourceOptions = ref<DataSource[]>([])
const selectedDatasourceId = ref(props.datasourceId || '')

// 指定会话模式的下拉选项：首次打开编辑/新建时拉取最近会话
const conversationOptions = ref<ConversationItem[]>([])
const conversationsLoaded = ref(false)
const loadConversationOptions = async (force = false) => {
  if (conversationsLoaded.value && !force) return
  try {
    const res = await getConversations('chat_data', { page: 1, page_size: 50, view: 'flat' })
    if (res.status === 'success') {
      const payload = res.data
      conversationOptions.value = Array.isArray(payload)
        ? payload
        : (payload.conversations || [])
      conversationsLoaded.value = true
    }
  } catch (err) {
    console.error('加载会话列表失败:', err)
  }
}

// 数据源组下拉：任务执行时选定的数据源（新会话模式会绑定到新建会话）。
// 打开弹窗时用 force 强制刷新，保证新加的数据源组能立即选到；缓存只服务于同一次弹窗
const loadDatasourceOptions = async (force = false) => {
  if (datasourceOptions.value.length && !force) return
  try {
    const res = await getDatasources({ include_tables: false })
    if (res.status === 'success') {
      datasourceOptions.value = res.data.datasources || []
    }
  } catch (err) {
    console.error('加载数据源列表失败:', err)
  }
}

// 模型与推理等级选项：来自运行时配置的模型注册表（含思考能力声明）
const modelOptions = ref<ChatDataModelOption[]>([])
const loadModelOptions = async (force = false) => {
  if (modelOptions.value.length && !force) return
  try {
    const res = await getChatDataConfig()
    if (res.status === 'success') {
      modelOptions.value = (res.data.models || []) as ChatDataModelOption[]
    }
  } catch (err) {
    console.error('加载模型列表失败:', err)
  }
}

const selectedModelThinking = computed(() =>
  modelOptions.value.find(option => option.id === form.value.modelId)?.thinking || null
)

// 推理等级选项：模型不支持推理时禁用；有声明档位用档位，否则给开关两档
const thinkingEffortOptions = computed(() => {
  const thinking = selectedModelThinking.value
  if (!thinking || thinking.mode === 'none') return []
  if (thinking.efforts?.length) return thinking.efforts
  return ['on', 'off']
})

const shortConversationId = (conversationId: string): string =>
  `${String(conversationId || '').slice(0, 8)}…`

const copyConversationId = async (conversationId: string) => {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(conversationId)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = conversationId
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      textarea.remove()
    }
    ElMessage.success('会话 ID 已复制')
  } catch (err) {
    console.error('复制会话 ID 失败:', err)
    ElMessage.error('复制失败，请手动复制')
  }
}

const loadJobs = async () => {
  loading.value = true
  try {
    const res = await getAgentSchedules()
    if (res.status === 'success') {
      jobs.value = res.data.jobs || []
      hasLoaded.value = true
    } else {
      ElMessage.error(res.msg || '获取定时任务失败')
    }
  } catch (err) {
    console.error('获取定时任务失败:', err)
    ElMessage.error('获取定时任务失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadJobs()
}

watch(visible, (value) => {
  if (value) void ensureLoaded()
})

const resetForm = () => {
  form.value = {
    title: '',
    prompt: '',
    conversationMode: props.conversationId ? 'fixed' : 'new_each_run',
    conversationId: props.conversationId || '',
    modelId: '',
    thinkingEffort: '',
    cronPreset: 'daily',
    hourlyMinute: 0,
    dailyTime: '09:00',
    weeklyDays: [1],
    monthlyDay: 1,
    cronExpr: '0 9 * * *',
    planTime: '',
    effectiveEndTime: ''
  }
  // 数据源默认取当前会话绑定的数据源：上次编辑残留的值不能带到新建表单
  selectedDatasourceId.value = props.datasourceId || ''
}

// 表单掌管的运行配置键：空值用 null 表达「清除该键、回退系统默认」
const currentRuntimeOptions = (): Record<string, unknown> => ({
  datasource_id: selectedDatasourceId.value || null,
  model_id: form.value.modelId || null,
  ...buildThinkingKeys(form.value.thinkingEffort)
})

const openCreate = () => {
  editingJobId.value = ''
  editingConversationTitle.value = ''
  resetForm()
  // 新建没有基线：调度与运行配置全量下发
  editorBaseline.value = createScheduleBaseline()
  void loadConversationOptions(true)
  void loadDatasourceOptions(true)
  void loadModelOptions(true)
  editorVisible.value = true
}

/** 从任务运行配置还原推理等级：显式档位优先，其次开关语义，都没有则留空 */
const resolveThinkingEffort = (jobOptions: Record<string, unknown>): string => {
  if (jobOptions.thinking_effort !== undefined && jobOptions.thinking_effort !== null) {
    return String(jobOptions.thinking_effort)
  }
  if (jobOptions.enable_thinking === true) return 'on'
  if (jobOptions.enable_thinking === false) return 'off'
  return ''
}

/** 编辑回显：任务字段 → 表单（cron 能反解析回预设档位时按结构化控件展示，否则回落自定义） */
const applyJobToForm = (job: AgentScheduleItem) => {
  const parsed = job.schedule_type === 'cron' ? parseCronToPreset(job.cron_expr || '') : null
  form.value = {
    title: job.title,
    prompt: job.prompt,
    conversationMode: job.conversation_mode || 'fixed',
    conversationId: job.conversation_id || '',
    modelId: '',
    thinkingEffort: '',
    cronPreset: parsed?.cronPreset || (job.schedule_type === 'once' ? 'once' : 'custom'),
    hourlyMinute: parsed?.hourlyMinute ?? 0,
    dailyTime: parsed?.dailyTime || '09:00',
    weeklyDays: parsed?.weeklyDays || [1],
    monthlyDay: parsed?.monthlyDay ?? 1,
    cronExpr: job.cron_expr || '0 9 * * *',
    planTime: job.plan_time || '',
    effectiveEndTime: job.effective_end_time || ''
  }
  selectedDatasourceId.value = job.datasource_id || ''
  const jobOptions = (job.options || {}) as Record<string, unknown>
  form.value.modelId = String(jobOptions.model_id || '')
  form.value.thinkingEffort = resolveThinkingEffort(jobOptions)
}

/** 编辑基线：取「表单当前值的规范化结果」，保证未改动的键不会因格式差异被下发 */
const buildBaselineFromJob = (job: AgentScheduleItem): ScheduleFormBaseline => ({
  options: {
    datasource_id: selectedDatasourceId.value || null,
    model_id: form.value.modelId || null,
    ...buildThinkingKeys(form.value.thinkingEffort)
  },
  scheduleType: job.schedule_type,
  cronExpr: job.cron_expr || '',
  planTime: localDateTime(job.plan_time) || '',
  effectiveEndTime: localDateTime(job.effective_end_time) || ''
})

const openEdit = (job: AgentScheduleItem) => {
  editingJobId.value = job.job_id
  editingConversationTitle.value = job.conversation_title || ''
  applyJobToForm(job)
  editorBaseline.value = buildBaselineFromJob(job)
  void loadConversationOptions(true)
  void loadDatasourceOptions(true)
  void loadModelOptions(true)
  editorVisible.value = true
}

const buildCronExpr = (): string => {
  if (form.value.cronPreset === 'hourly') {
    return `${Number(form.value.hourlyMinute) % 60} * * * *`
  }
  if (form.value.cronPreset === 'weekly' || form.value.cronPreset === 'monthly') {
    const [hour = '', minute = ''] = form.value.dailyTime.split(':')
    if (form.value.cronPreset === 'weekly') {
      // 周日(0)排末尾，与预览文案「周一→周日」顺序一致（cron 语义不受顺序影响）
      const days = [...new Set(form.value.weeklyDays)].sort((a, b) => (a === 0 ? 7 : a) - (b === 0 ? 7 : b))
      return `${Number(minute)} ${Number(hour)} * * ${days.join(',')}`
    }
    return `${Number(minute)} ${Number(hour)} ${Number(form.value.monthlyDay)} * *`
  }
  if (form.value.cronPreset === 'daily') {
    const [hour = '', minute = ''] = form.value.dailyTime.split(':')
    return `${Number(minute)} ${Number(hour)} * * *`
  }
  return form.value.cronExpr.trim()
}

// 行内预览：把当前调度选择翻译成自然语言，供用户确认语义
const schedulePreviewText = computed(() => schedulePresetPreview(form.value))

const localDateTime = (value: string | Date | null | undefined): string | null => {
  if (!value) return null
  // el-date-picker 交互后产出 Date：按本地时区转成后端期望的朴素时间字符串
  if (value instanceof Date) {
    const pad = (num: number) => String(num).padStart(2, '0')
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:00`
  }
  const text = String(value).trim()
  if (!text) return null
  // 后端按服务器本地时区存储，时间为无时区的本地时间
  return text.replace('T', ' ').slice(0, 19)
}

const submitJob = async () => {
  const title = form.value.title.trim()
  const prompt = form.value.prompt.trim()
  if (!title) {
    ElMessage.warning('请填写任务标题')
    return
  }
  if (!prompt) {
    ElMessage.warning('请填写任务指令')
    return
  }
  // 新建与编辑共用一份 payload；编辑只下发与打开弹窗时基线不同的字段，
  // 未改动的调度/运行配置不提交，避免用表单默认值覆盖任务里其它配置
  const scheduleType: 'cron' | 'once' = form.value.cronPreset === 'once' ? 'once' : 'cron'
  const payload: Partial<AgentSchedulePayload> & Pick<AgentSchedulePayload, 'title' | 'prompt' | 'schedule_type'> = {
    title,
    prompt,
    schedule_type: scheduleType,
    conversation_mode: form.value.conversationMode
  }
  if (form.value.conversationMode === 'fixed') {
    if (!form.value.conversationId) {
      ElMessage.warning('请选择一个历史会话')
      return
    }
    payload.conversation_id = form.value.conversationId
  }
  if (form.value.cronPreset === 'weekly' && !form.value.weeklyDays.length) {
    ElMessage.warning('请至少选择一个执行日')
    return
  }
  if (scheduleType === 'once' && !localDateTime(form.value.planTime)) {
    ElMessage.warning('请选择单次执行时间')
    return
  }
  const schedulePatch = buildSchedulePatch({
    scheduleType,
    cronExpr: buildCronExpr(),
    planTime: localDateTime(form.value.planTime) || '',
    effectiveEndTime: localDateTime(form.value.effectiveEndTime) || ''
  }, editorBaseline.value)
  Object.assign(payload, schedulePatch)
  const optionsPatch = buildRuntimeOptionsPatch(currentRuntimeOptions(), editorBaseline.value.options)
  if (Object.keys(optionsPatch).length) payload.options = optionsPatch

  savingJob.value = true
  try {
    const res = editingJobId.value
      ? await updateAgentSchedule(editingJobId.value, payload)
      : await createAgentSchedule(payload)
    if (res.status === 'success') {
      ElMessage.success(editingJobId.value ? '任务已更新' : '任务已创建')
      editorVisible.value = false
      await loadJobs()
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    console.error('保存定时任务失败:', err)
    ElMessage.error('保存定时任务失败，请稍后重试')
  } finally {
    savingJob.value = false
  }
}

const toggleJob = async (job: AgentScheduleItem, enabled: boolean) => {
  try {
    const res = await updateAgentSchedule(job.job_id, { enabled_flag: enabled ? 1 : 0 })
    if (res.status === 'success') {
      ElMessage.success(enabled ? '任务已启用' : '任务已停用')
      await loadJobs()
    } else {
      ElMessage.error(res.msg || '操作失败')
      await loadJobs()
    }
  } catch (err) {
    console.error('切换定时任务状态失败:', err)
    ElMessage.error('操作失败，请稍后重试')
  }
}

const removeJob = async (job: AgentScheduleItem) => {
  try {
    await ElMessageBox.confirm(`确定删除定时任务「${job.title}」？删除后不再执行。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  try {
    const res = await deleteAgentSchedule(job.job_id)
    if (res.status === 'success') {
      ElMessage.success('已删除')
      await loadJobs()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    console.error('删除定时任务失败:', err)
    ElMessage.error('删除失败，请稍后重试')
  }
}

const triggerNow = async (job: AgentScheduleItem) => {
  if (triggeringJobId.value) return
  triggeringJobId.value = job.job_id
  try {
    const res = await triggerAgentScheduleNow(job.job_id)
    if (res.status === 'success') {
      if (res.data.run_id) {
        ElMessage.success('已触发执行，结果将写入绑定会话')
      } else {
        ElMessage.warning(res.msg || '会话正忙，已加入队列稍后自动执行')
      }
      await loadJobs()
    } else {
      ElMessage.error(res.msg || '立即执行失败')
    }
  } catch (err) {
    console.error('立即执行定时任务失败:', err)
    ElMessage.error('立即执行失败，请稍后重试')
  } finally {
    triggeringJobId.value = ''
  }
}

const loadRuns = async (jobId: string) => {
  runsLoading.value = true
  try {
    const res = await getAgentScheduleRuns(jobId)
    if (res.status === 'success') {
      runs.value = res.data.runs || []
    } else {
      ElMessage.error(res.msg || '获取执行历史失败')
    }
  } catch (err) {
    console.error('获取执行历史失败:', err)
    ElMessage.error('获取执行历史失败，请稍后重试')
  } finally {
    runsLoading.value = false
  }
}

const openRuns = async (job: AgentScheduleItem) => {
  runsJob.value = job
  runs.value = []
  runsVisible.value = true
  await loadRuns(job.job_id)
}

defineExpose({ ensureLoaded })
</script>

<template>
  <el-dialog
    v-model="visible"
    title="定时任务"
    width="min(920px, 94vw)"
    :append-to-body="true"
  >
    <div class="flex flex-col gap-1 pb-3">
      <div class="flex items-center justify-between">
        <p class="m-0 text-xs text-slate-500">到点后自动在绑定会话内执行一次智能问数，结果留在会话中。</p>
      <el-button type="primary" size="small" round @click="openCreate">
        <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
        新建定时任务
      </el-button>
      </div>
    </div>

    <el-table :data="jobs" v-loading="loading" size="small" style="width: 100%" max-height="60vh" empty-text="暂无定时任务">
      <el-table-column label="任务名称" min-width="130" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="text-sm font-medium text-slate-800">{{ row.title }}</span>
        </template>
      </el-table-column>
      <el-table-column label="会话" min-width="130" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.conversation_mode === 'new_each_run'" class="text-xs text-slate-500">每次新建会话</span>
          <button
            v-else
            type="button"
            class="border-none bg-transparent p-0 text-left text-xs text-[#1f82d9] cursor-pointer transition-colors hover:text-[#1668b3]"
            :title="row.conversation_title ? `点击打开会话` : '会话不存在'"
            @click="emit('openConversation', row.conversation_id)"
          >{{ row.conversation_title || '会话已删除' }}</button>
        </template>
      </el-table-column>
      <el-table-column label="会话ID" width="100">
        <template #default="{ row }">
          <span
            v-if="row.conversation_id"
            class="cursor-pointer font-mono text-xs text-slate-500"
            :title="`${row.conversation_id}（点击复制）`"
            @click="copyConversationId(row.conversation_id)"
          >{{ shortConversationId(row.conversation_id) }}</span>
          <span v-else class="text-xs text-slate-400">-</span>
        </template>
      </el-table-column>
      <el-table-column label="调度" min-width="110" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="text-xs text-slate-600" :title="row.effective_end_time ? `有效期至 ${formatDateTimeValue(row.effective_end_time)}` : '长期有效'">
            {{ scheduleText(row) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="下次执行" width="155">
        <template #default="{ row }">
          <span v-if="row.next_trigger_time" class="text-xs text-[#1f82d9]">{{ formatDateTimeValue(row.next_trigger_time) }}</span>
          <span v-else class="text-xs text-slate-400">-</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="65" align="center">
        <template #default="{ row }">
          <el-switch
            :model-value="row.enabled_flag === 1"
            size="small"
            @change="(value: any) => toggleJob(row, !!value)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-dropdown trigger="click" @command="(command: string) => command === 'edit' ? openEdit(row) : command === 'runs' ? openRuns(row) : command === 'trigger' ? triggerNow(row) : removeJob(row)">
            <el-button link size="small" class="!px-1 !text-slate-400 hover:!text-slate-600 focus-visible:!outline-none">
              <i class="fa-solid fa-ellipsis-vertical text-xs"></i>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="trigger">立即执行</el-dropdown-item>
                <el-dropdown-item command="runs">执行历史</el-dropdown-item>
                <el-dropdown-item command="edit">编辑</el-dropdown-item>
                <el-dropdown-item command="delete" divided>
                  <span class="text-red-500">删除</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建 / 编辑 -->
    <el-dialog
      v-model="editorVisible"
      :title="editingJobId ? '编辑定时任务' : '新建定时任务'"
      width="min(640px, 92vw)"
      :append-to-body="true"
    >
      <div class="flex flex-col gap-3">
        <div>
          <label class="mb-1 block text-xs font-medium text-slate-600">运行会话<span class="ml-1 font-normal text-slate-400">（到点后在所选会话内执行）</span></label>
          <el-radio-group v-model="form.conversationMode" size="small">
            <el-radio-button value="new_each_run">每次新建会话</el-radio-button>
            <el-radio-button value="fixed">指定历史会话</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="form.conversationMode === 'fixed'">
          <el-select
            v-model="form.conversationId"
            filterable
            placeholder="选择历史会话"
            size="small"
            class="!w-full"
          >
            <el-option
              v-for="conv in conversationOptions"
              :key="conv.id"
              :label="conv.title"
              :value="conv.id"
            />
            <el-option
              v-if="form.conversationId && !conversationOptions.some(option => option.id === form.conversationId)"
              :key="form.conversationId"
              :label="editingConversationTitle || form.conversationId"
              :value="form.conversationId"
            />
          </el-select>
        </div>
        <div>
          <label class="mb-1 block text-xs font-medium text-slate-600">任务标题</label>
          <el-input v-model="form.title" maxlength="100" placeholder="例如：每日销售日报" />
        </div>
        <div>
          <label class="mb-1 block text-xs font-medium text-slate-600">数据源组<span class="ml-1 font-normal text-slate-400">（任务执行时查询数据所用；纯对话任务可清空）</span></label>
          <el-select
            v-model="selectedDatasourceId"
            filterable
            clearable
            placeholder="选择数据源组"
            size="small"
            class="!w-full"
          >
            <el-option
              v-for="ds in datasourceOptions"
              :key="ds.id"
              :label="ds.group_name"
              :value="ds.id"
            />
          </el-select>
        </div>
        <div>
          <label class="mb-1 block text-xs font-medium text-slate-600">指令</label>
          <el-input
            v-model="form.prompt"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="例如：汇总昨日的销售额和订单量，生成简要分析"
          />
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <div class="min-w-0 flex-1">
            <label class="mb-1 block text-xs font-medium text-slate-600">模型<span class="ml-1 font-normal text-slate-400">（留空使用系统默认）</span></label>
            <el-select
              v-model="form.modelId"
              filterable
              clearable
              placeholder="系统默认"
              size="small"
              class="!w-full"
              @change="form.thinkingEffort = ''"
            >
              <el-option
                v-for="model in modelOptions"
                :key="model.id"
                :label="model.label"
                :value="model.id"
              />
            </el-select>
          </div>
          <div class="min-w-0 flex-1">
            <label class="mb-1 block text-xs font-medium text-slate-600">推理等级</label>
            <el-select
              v-model="form.thinkingEffort"
              clearable
              placeholder="跟随模型默认"
              size="small"
              class="!w-full"
              :disabled="!thinkingEffortOptions.length"
            >
              <el-option
                v-for="effort in thinkingEffortOptions"
                :key="effort"
                :label="effort === 'on' ? '开启' : effort === 'off' ? '关闭' : effort"
                :value="effort"
              />
            </el-select>
          </div>
        </div>
        <div>
          <label class="mb-1 block text-xs font-medium text-slate-600">调度</label>
          <div class="flex flex-wrap items-center gap-2">
            <el-select v-model="form.cronPreset" size="small" class="!w-28">
              <el-option label="每小时" value="hourly" />
              <el-option label="每天" value="daily" />
              <el-option label="每周" value="weekly" />
              <el-option label="每月" value="monthly" />
              <el-option label="单次执行" value="once" />
              <el-option label="自定义" value="custom" />
            </el-select>
            <template v-if="form.cronPreset === 'hourly'">
              <span class="text-xs text-slate-500">第</span>
              <el-select v-model="form.hourlyMinute" size="small" class="!w-20">
                <el-option v-for="minute in 60" :key="minute" :label="String(minute - 1).padStart(2, '0')" :value="minute - 1" />
              </el-select>
              <span class="text-xs text-slate-500">分钟</span>
            </template>
            <template v-else-if="form.cronPreset === 'weekly'">
              <el-select v-model="form.weeklyDays" multiple size="small" class="!w-96" placeholder="选择周几">
                <template #label="{ value }">{{ weeklyDayShortLabel(value) }}</template>
                <el-option v-for="option in WEEKLY_DAY_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
              <el-time-picker v-model="form.dailyTime" format="HH:mm" value-format="HH:mm" placeholder="时间" size="small" class="!w-24" />
            </template>
            <template v-else-if="form.cronPreset === 'monthly'">
              <el-select v-model="form.monthlyDay" size="small" class="!w-24">
                <el-option v-for="day in 31" :key="day" :label="`${day} 号`" :value="day" />
              </el-select>
              <el-time-picker v-model="form.dailyTime" format="HH:mm" value-format="HH:mm" placeholder="时间" size="small" class="!w-24" />
            </template>
            <template v-else-if="form.cronPreset === 'daily'">
              <el-time-picker v-model="form.dailyTime" format="HH:mm" value-format="HH:mm" placeholder="时间" size="small" class="!w-28" />
            </template>
            <template v-else-if="form.cronPreset === 'once'">
              <el-date-picker
                v-model="form.planTime"
                type="datetime"
                format="YYYY-MM-DD HH:mm"
                placeholder="选择执行时间"
                size="small"
                class="!w-56"
              />
            </template>
            <template v-else>
              <el-input v-model="form.cronExpr" size="small" class="!w-48" placeholder="分 时 日 月 周，如 0 9 * * 1-5" />
            </template>
            <span v-if="schedulePreviewText" class="text-xs text-slate-400">{{ schedulePreviewText }}</span>
          </div>
        </div>
        <div>
          <label class="mb-1 block text-xs text-slate-500">有效期至（可选，留空表示长期有效）</label>
          <el-date-picker
            v-model="form.effectiveEndTime"
            type="datetime"
            format="YYYY-MM-DD HH:mm"
            placeholder="长期有效"
            class="!w-full"
          />
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="editorVisible = false">取消</el-button>
        <el-button type="primary" size="small" :loading="savingJob" @click="submitJob">
          {{ editingJobId ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 执行历史 -->
    <el-dialog
      v-model="runsVisible"
      width="min(860px, 94vw)"
      :append-to-body="true"
    >
      <template #header>
        <div class="flex items-center gap-2">
          <span class="text-base font-medium text-slate-800">执行历史 · {{ runsJob?.title || '' }}</span>
          <el-button
            link
            size="small"
            :loading="runsLoading"
            :disabled="!runsJob"
            @click="runsJob && loadRuns(runsJob.job_id)"
          >
            <i v-if="!runsLoading" class="fa-solid fa-rotate-right text-[11px]" style="margin-right: 4px;"></i>刷新
          </el-button>
        </div>
      </template>
      <AgentScheduleRunsTable :runs="runs" :loading="runsLoading" @copy-conversation-id="copyConversationId" />
    </el-dialog>
  </el-dialog>
</template>
