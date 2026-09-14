<script setup lang="ts">
/**
 * 后台管理 · 定时任务面板。
 * 全局概览统计 + 一键暂停/恢复调度 + 全量任务列表（跨用户）与单任务启停。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getAdminAgentSchedules,
  getAdminAgentScheduleRuns,
  getAgentScheduleAdminOverview,
  setAdminAgentScheduleEnabled,
  setAgentSchedulePaused
} from '../api'
import AgentScheduleRunsTable from './AgentScheduleRunsTable.vue'
import type { AgentScheduleItem, AgentScheduleOverview, AgentScheduleRunItem } from '../types'
import { formatDateTimeValue } from '../utils'
import { getScheduleRunStatusClass, getScheduleRunStatusText, scheduleText } from '../utils/agentSchedule'

const loading = ref(false)
const overview = ref<AgentScheduleOverview | null>(null)
const jobs = ref<AgentScheduleItem[]>([])
const keyword = ref('')
const hasLoaded = ref(false)
const togglingPause = ref(false)
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

// 列表过滤：启停状态 / 是否过期（空串=不过滤，服务端分页口径）
const enabledFilter = ref<'' | 'on' | 'off'>('')
const expiredFilter = ref<'' | 'active' | 'expired'>('')

const runsVisible = ref(false)
const runsLoading = ref(false)
const runsJobTitle = ref('')
const runsJobId = ref('')
const runs = ref<AgentScheduleRunItem[]>([])

const successRate = computed(() => {
  if (!overview.value || !overview.value.total_runs) return '-'
  const rate = (overview.value.succeeded_runs / overview.value.total_runs) * 100
  return `${rate.toFixed(1)}%`
})

const statCards = computed(() => {
  const data = overview.value
  return [
    { label: '定时任务', value: data ? `${data.enabled_jobs}/${data.total_jobs}` : '-', hint: '启用中 / 总数' },
    { label: '累计执行', value: data ? String(data.total_runs) : '-', hint: `近24h ${data?.runs_last_24h ?? 0} 次` },
    { label: '执行成功率', value: successRate.value, hint: `失败 ${data?.failed_runs ?? 0} · 取消 ${data?.cancelled_runs ?? 0} · 跳过 ${data?.skipped_runs ?? 0}` },
    { label: '执行中', value: data ? String(data.running_runs) : '-', hint: '排队/领取/执行中' }
  ]
})

const paused = computed(() => !!overview.value?.paused_flag)

const loadJobs = async () => {
  const res = await getAdminAgentSchedules({
    keyword: keyword.value.trim() || undefined,
    limit: pageSize.value,
    offset: (currentPage.value - 1) * pageSize.value,
    enabled: enabledFilter.value === '' ? undefined : enabledFilter.value === 'on',
    expired: expiredFilter.value === '' ? undefined : expiredFilter.value === 'expired'
  })
  if (res.status === 'success') {
    jobs.value = res.data.jobs || []
    total.value = res.data.total || 0
    hasLoaded.value = true
  } else {
    ElMessage.error(res.msg || '获取任务列表失败')
  }
}

const handleFilterChange = async () => {
  currentPage.value = 1
  loading.value = true
  try {
    await loadJobs()
  } finally {
    loading.value = false
  }
}

const loadAll = async () => {
  loading.value = true
  try {
    const overviewRes = await getAgentScheduleAdminOverview()
    if (overviewRes.status === 'success') {
      overview.value = overviewRes.data
    } else {
      ElMessage.error(overviewRes.msg || '获取概览失败')
    }
    await loadJobs()
  } catch (err) {
    console.error('获取定时任务后台数据失败:', err)
    ElMessage.error('获取定时任务数据失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadAll()
}

const searchJobs = async () => {
  currentPage.value = 1
  loading.value = true
  try {
    await loadJobs()
  } catch (err) {
    console.error('搜索定时任务失败:', err)
    ElMessage.error('搜索失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const handlePageChange = async (page: number) => {
  currentPage.value = page
  loading.value = true
  try {
    await loadJobs()
  } catch (err) {
    console.error('切换定时任务分页失败:', err)
    ElMessage.error('获取任务列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const handleSizeChange = async (size: number) => {
  pageSize.value = size
  currentPage.value = 1
  await handlePageChange(1)
}

const togglePause = async (value: boolean) => {
  if (value) {
    try {
      await ElMessageBox.confirm(
        '停止后所有定时任务将不再触发新的执行；进行中的执行与已排队的触发不受影响，恢复后继续。确定停止调度？',
        '停止调度确认',
        { type: 'warning', confirmButtonText: '停止调度', cancelButtonText: '取消' }
      )
    } catch {
      return
    }
  }
  togglingPause.value = true
  try {
    const res = await setAgentSchedulePaused({ paused: value })
    if (res.status === 'success') {
      ElMessage.success(value ? '已停止任务调度' : '已恢复任务调度')
      await loadAll()
    } else {
      ElMessage.error(res.msg || '操作失败')
    }
  } catch (err) {
    console.error('切换任务调度开关失败:', err)
    ElMessage.error('操作失败，请稍后重试')
  } finally {
    togglingPause.value = false
  }
}

const toggleJob = async (job: AgentScheduleItem, enabled: boolean) => {
  try {
    const res = await setAdminAgentScheduleEnabled(job.job_id, enabled ? 1 : 0)
    if (res.status === 'success') {
      ElMessage.success(enabled ? '任务已启用' : '任务已停用')
      await loadAll()
    } else {
      ElMessage.error(res.msg || '操作失败')
      await loadAll()
    }
  } catch (err) {
    console.error('后台启停定时任务失败:', err)
    ElMessage.error('操作失败，请稍后重试')
  }
}

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

const loadRuns = async (jobId: string) => {
  runsLoading.value = true
  try {
    const res = await getAdminAgentScheduleRuns(jobId)
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
  runsJobTitle.value = job.title
  runsJobId.value = job.job_id
  runs.value = []
  runsVisible.value = true
  await loadRuns(job.job_id)
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 全局暂停 + 概览 -->
    <div class="shrink-0 border-b border-slate-100 px-4 pb-3 pt-3">
      <div class="mb-3 flex items-center gap-3">
        <div class="flex min-w-0 flex-1 items-center gap-2">
          <span
            :class="[
              'inline-flex h-5 items-center rounded-full px-2 text-[11px]',
              paused ? 'bg-amber-50 text-amber-700' : 'bg-emerald-50 text-emerald-700'
            ]"
          >
            <i :class="['mr-1 text-[9px]', paused ? 'fa-solid fa-pause' : 'fa-solid fa-play']"></i>
            {{ paused ? '任务调度已停止' : '调度运行中' }}
          </span>
          <span v-if="overview?.pause_reason" class="min-w-0 truncate text-xs text-slate-400">
            {{ overview.pause_reason }}
          </span>
        </div>
        <span class="shrink-0 text-xs text-slate-500">停止任务调度</span>
        <el-switch
          :model-value="paused"
          :loading="togglingPause"
          active-text=""
          @change="(value: any) => togglePause(!!value)"
        />
      </div>
      <div class="grid grid-cols-4 gap-2">
        <div v-for="card in statCards" :key="card.label" class="rounded-lg bg-slate-50 px-3 py-2">
          <div class="text-[11px] text-slate-400">{{ card.label }}</div>
          <div class="text-base font-medium text-slate-700">{{ card.value }}</div>
          <div class="text-[10px] text-slate-400">{{ card.hint }}</div>
        </div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="flex shrink-0 items-center gap-2 px-4 py-2">
      <el-input
        v-model="keyword"
        size="small"
        placeholder="搜索标题或用户"
        clearable
        class="!w-56"
        @keyup.enter="searchJobs"
        @clear="searchJobs"
      />
      <el-select v-model="enabledFilter" size="small" class="!w-28" placeholder="开启状态" @change="handleFilterChange">
        <el-option label="全部状态" value="" />
        <el-option label="已开启" value="on" />
        <el-option label="已停用" value="off" />
      </el-select>
      <el-select v-model="expiredFilter" size="small" class="!w-28" placeholder="是否过期" @change="handleFilterChange">
        <el-option label="全部期限" value="" />
        <el-option label="未过期" value="active" />
        <el-option label="已过期" value="expired" />
      </el-select>
      <el-button size="small" @click="searchJobs">搜索</el-button>
      <el-button size="small" :loading="loading" @click="loadAll">
        <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
      </el-button>
    </div>

    <!-- 任务列表：表格化，任务量增长后仍可扫读与扩展列 -->
    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <div class="min-h-0 flex-1">
        <el-table
          :data="jobs"
          v-loading="loading"
          size="small"
          height="100%"
          style="width: 100%"
          empty-text="暂无定时任务"
        >
        <el-table-column label="任务标题" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-sm font-medium text-slate-800">{{ row.title }}</span>
          </template>
        </el-table-column>
        <el-table-column label="绑定会话" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">
            <span
              v-if="row.conversation_mode === 'new_each_run'"
              class="text-xs text-slate-500"
              title="每次执行新建会话"
            >每次新建会话</span>
            <span v-else class="text-xs text-slate-600" :title="`会话ID ${row.conversation_id}`">{{ row.conversation_title || '会话已删除' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建人" width="100" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500" :title="`用户ID ${row.user_id}`">{{ row.create_by_name || row.user_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="调度" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ scheduleText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="有效期" width="150">
          <template #default="{ row }">
            <span v-if="row.effective_end_time" class="text-xs text-slate-600">{{ formatDateTimeValue(row.effective_end_time) }}</span>
            <span v-else class="text-xs text-slate-400">长期有效</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled_flag === 1"
              size="small"
              @change="(value: any) => toggleJob(row, !!value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="执行次数" width="85" align="right">
          <template #default="{ row }">{{ row.total_count || 0 }}</template>
        </el-table-column>
        <el-table-column label="成功" width="65" align="right">
          <template #default="{ row }">
            <span class="text-emerald-600">{{ row.succeeded_count || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="失败" width="65" align="right">
          <template #default="{ row }">
            <span :class="row.failed_count ? 'text-red-500 font-medium' : 'text-slate-400'">{{ row.failed_count || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="取消" width="65" align="right">
          <template #default="{ row }">
            <span :class="row.cancelled_count ? 'text-amber-500 font-medium' : 'text-slate-400'">{{ row.cancelled_count || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最近执行" min-width="215">
          <template #default="{ row }">
            <div v-if="row.last_run_time" class="flex items-center gap-2 text-xs">
              <span class="text-slate-500">{{ formatDateTimeValue(row.last_run_time) }}</span>
              <span
                v-if="row.last_run_status"
                :class="['shrink-0 rounded px-1.5 py-0.5 text-[10px]', getScheduleRunStatusClass(row.last_run_status)]"
                title="最近一次执行结果"
              >{{ getScheduleRunStatusText(row.last_run_status) }}</span>
            </div>
            <span v-else class="text-xs text-slate-400">未执行</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="95" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" @click="openRuns(row)">
              <i class="fa-solid fa-list-check text-[11px]"></i>
              执行历史
            </el-button>
          </template>
        </el-table-column>
        </el-table>
      </div>
      <div class="flex shrink-0 justify-end pt-2">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          size="small"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>

    <!-- 执行历史 -->
    <el-dialog
      v-model="runsVisible"
      width="min(960px, 94vw)"
      :append-to-body="true"
    >
      <template #header>
        <div class="flex items-center gap-2">
          <span class="text-base font-medium text-slate-800">执行历史 · {{ runsJobTitle }}</span>
          <el-button
            link
            size="small"
            :loading="runsLoading"
            :disabled="!runsJobId"
            @click="runsJobId && loadRuns(runsJobId)"
          >
            <i v-if="!runsLoading" class="fa-solid fa-rotate-right text-[11px]" style="margin-right: 4px;"></i>刷新
          </el-button>
        </div>
      </template>
      <AgentScheduleRunsTable :runs="runs" :loading="runsLoading" @copy-conversation-id="copyConversationId" />
    </el-dialog>
  </div>
</template>
