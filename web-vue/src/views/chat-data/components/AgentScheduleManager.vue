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
  triggerAgentScheduleNow,
  updateAgentSchedule
} from '../api'
import type { AgentScheduleItem, AgentScheduleRunItem } from '../types'
import { formatDateTimeValue } from '../utils'
import { WEEKLY_DAY_OPTIONS, scheduleText } from '../utils/agentSchedule'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 新建任务时默认绑定的会话；为空时不允许创建 */
  conversationId: { type: String, default: '' },
  /** 当前会话绑定的数据源，创建任务时随运行选项带上 */
  datasourceId: { type: String, default: '' }
})

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

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
  scheduleType: 'cron' as 'cron' | 'once',
  cronPreset: 'daily' as 'hourly' | 'daily' | 'weekly' | 'custom',
  hourlyMinute: 0,
  dailyTime: '09:00',
  weeklyDay: 1,
  cronExpr: '0 9 * * *',
  planTime: '',
  effectiveEndTime: ''
})

const runsVisible = ref(false)
const runsLoading = ref(false)
const runsJob = ref<AgentScheduleItem | null>(null)
const runs = ref<AgentScheduleRunItem[]>([])

// 立即执行防连点：同一时刻只允许一个在途触发请求，避免堆积多个手动实例
const triggeringJobId = ref('')

const canCreate = computed(() => !!props.conversationId)

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
    scheduleType: 'cron',
    cronPreset: 'daily',
    hourlyMinute: 0,
    dailyTime: '09:00',
    weeklyDay: 1,
    cronExpr: '0 9 * * *',
    planTime: '',
    effectiveEndTime: ''
  }
}

const openCreate = () => {
  if (!canCreate.value) {
    ElMessage.warning('请先选择或新建一个会话，再创建定时任务')
    return
  }
  editingJobId.value = ''
  resetForm()
  editorVisible.value = true
}

const openEdit = (job: AgentScheduleItem) => {
  editingJobId.value = job.job_id
  form.value = {
    title: job.title,
    prompt: job.prompt,
    scheduleType: job.schedule_type,
    cronPreset: 'custom',
    hourlyMinute: 0,
    dailyTime: '09:00',
    weeklyDay: 1,
    cronExpr: job.cron_expr || '0 9 * * *',
    planTime: job.plan_time || '',
    effectiveEndTime: job.effective_end_time || ''
  }
  editorVisible.value = true
}

const buildCronExpr = (): string => {
  if (form.value.cronPreset !== 'custom') {
    if (form.value.cronPreset === 'hourly') {
      return `${Number(form.value.hourlyMinute) % 60} * * * *`
    }
    const [hour = '', minute = ''] = form.value.dailyTime.split(':')
    if (form.value.cronPreset === 'daily') {
      return `${Number(minute)} ${Number(hour)} * * *`
    }
    return `${Number(minute)} ${Number(hour)} * * ${form.value.weeklyDay}`
  }
  return form.value.cronExpr.trim()
}

const localDateTime = (value: string | Date): string | null => {
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
  // 编辑时只提交表单可见字段：数据源/运行选项绑定创建时会话，编辑不再覆盖，
  // 避免用户切换会话后编辑任务导致执行数据源被静默篡改
  const payload: Record<string, unknown> = editingJobId.value
    ? {
        title,
        prompt,
        schedule_type: form.value.scheduleType
      }
    : {
        title,
        prompt,
        conversation_id: props.conversationId,
        schedule_type: form.value.scheduleType,
        datasource_id: props.datasourceId || undefined,
        options: props.datasourceId ? { datasource_id: props.datasourceId } : undefined
      }
  if (form.value.scheduleType === 'once') {
    const planTime = localDateTime(form.value.planTime)
    if (!planTime) {
      ElMessage.warning('请选择单次执行时间')
      return
    }
    payload.plan_time = planTime
  } else {
    payload.cron_expr = buildCronExpr()
  }
  const effectiveEnd = localDateTime(form.value.effectiveEndTime)
  if (effectiveEnd) payload.effective_end_time = effectiveEnd

  savingJob.value = true
  try {
    const res = editingJobId.value
      ? await updateAgentSchedule(editingJobId.value, payload)
      : await createAgentSchedule(payload as any)
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

const openRuns = async (job: AgentScheduleItem) => {
  runsJob.value = job
  runs.value = []
  runsVisible.value = true
  runsLoading.value = true
  try {
    const res = await getAgentScheduleRuns(job.job_id)
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

defineExpose({ ensureLoaded })
</script>

<template>
  <el-dialog
    v-model="visible"
    title="定时任务"
    width="min(680px, 92vw)"
    :append-to-body="true"
  >
    <div class="flex items-center justify-between pb-3">
      <p class="m-0 text-xs text-slate-500">到点后自动在绑定会话内执行一次智能问数，结果留在会话中。</p>
      <el-button type="primary" size="small" round :disabled="!canCreate" @click="openCreate">
        <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
        新建定时任务
      </el-button>
    </div>

    <div v-if="loading" class="flex flex-col items-center gap-2 py-10 text-xs text-slate-400">
      <i class="fa-solid fa-spinner fa-spin text-2xl opacity-50"></i>
      <span>加载定时任务...</span>
    </div>

    <div v-else-if="jobs.length === 0" class="flex flex-col items-center gap-2 py-10 text-xs text-slate-400">
      <i class="fa-regular fa-clock text-2xl opacity-50"></i>
      <span>暂无定时任务</span>
    </div>

    <div v-else class="max-h-[52vh] overflow-y-auto pr-1">
      <div
        v-for="job in jobs"
        :key="job.job_id"
        class="mb-2 rounded-lg border border-slate-200 px-3 py-2.5 transition-colors hover:border-[#1f82d9]/40"
      >
        <div class="flex items-center gap-2">
          <span class="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">{{ job.title }}</span>
          <el-switch
            :model-value="job.enabled_flag === 1"
            size="small"
            @change="(value: any) => toggleJob(job, !!value)"
          />
          <el-dropdown trigger="click" @command="(command: string) => command === 'edit' ? openEdit(job) : command === 'runs' ? openRuns(job) : command === 'trigger' ? triggerNow(job) : removeJob(job)">
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
        </div>
        <div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
          <span class="inline-flex items-center gap-1">
            <i class="fa-regular fa-clock text-[10px]"></i>{{ scheduleText(job) }}
          </span>
          <span v-if="job.next_trigger_time" class="text-[#1f82d9]">
            下次 {{ formatDateTimeValue(job.next_trigger_time) }}
          </span>
          <span v-if="job.effective_end_time" class="text-slate-400">
            有效期至 {{ formatDateTimeValue(job.effective_end_time) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 新建 / 编辑 -->
    <el-dialog
      v-model="editorVisible"
      :title="editingJobId ? '编辑定时任务' : '新建定时任务'"
      width="min(520px, 92vw)"
      :append-to-body="true"
    >
      <div class="flex flex-col gap-3">
        <div>
          <label class="mb-1 block text-xs font-medium text-slate-600">任务标题</label>
          <el-input v-model="form.title" maxlength="100" placeholder="例如：每日销售日报" />
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
        <div>
          <label class="mb-1 block text-xs font-medium text-slate-600">调度<span class="ml-1 font-normal text-slate-400">（按服务器时区执行）</span></label>
          <el-radio-group v-model="form.scheduleType" size="small">
            <el-radio-button value="cron">周期执行</el-radio-button>
            <el-radio-button value="once">单次执行</el-radio-button>
          </el-radio-group>
        </div>
        <template v-if="form.scheduleType === 'cron'">
          <div class="flex flex-wrap items-center gap-2">
            <el-select v-model="form.cronPreset" size="small" class="w-28">
              <el-option label="每小时" value="hourly" />
              <el-option label="每天" value="daily" />
              <el-option label="每周" value="weekly" />
              <el-option label="自定义" value="custom" />
            </el-select>
            <template v-if="form.cronPreset === 'hourly'">
              <span class="text-xs text-slate-500">每小时的第</span>
              <el-input-number v-model="form.hourlyMinute" :min="0" :max="59" size="small" controls-position="right" class="w-20" />
              <span class="text-xs text-slate-500">分</span>
            </template>
            <template v-else-if="form.cronPreset === 'daily'">
              <el-time-picker v-model="form.dailyTime" format="HH:mm" value-format="HH:mm" placeholder="时间" size="small" class="w-28" />
            </template>
            <template v-else-if="form.cronPreset === 'weekly'">
              <el-select v-model="form.weeklyDay" size="small" class="w-24">
                <el-option v-for="option in WEEKLY_DAY_OPTIONS" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
              <el-time-picker v-model="form.dailyTime" format="HH:mm" value-format="HH:mm" placeholder="时间" size="small" class="w-28" />
            </template>
            <template v-else>
              <el-input v-model="form.cronExpr" size="small" class="w-48" placeholder="分 时 日 月 周，如 0 9 * * 1-5" />
            </template>
          </div>
        </template>
        <template v-else>
          <el-date-picker
            v-model="form.planTime"
            type="datetime"
            format="YYYY-MM-DD HH:mm"
            placeholder="选择执行时间"
            class="!w-full"
          />
        </template>
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
      :title="`执行历史 · ${runsJob?.title || ''}`"
      width="min(860px, 94vw)"
      :append-to-body="true"
    >
      <AgentScheduleRunsTable :runs="runs" :loading="runsLoading" />
    </el-dialog>
  </el-dialog>
</template>
