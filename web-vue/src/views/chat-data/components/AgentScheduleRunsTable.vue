<script setup lang="ts">
/**
 * 定时任务执行历史表格。
 * 后台管理面板与用户侧管理弹窗共用，保持字段口径与展示一致。
 */
import type { AgentScheduleRunItem } from '../types'
import { formatDateTimeValue } from '../utils'
import {
  getScheduleRunStatusClass,
  getScheduleRunStatusText,
  scheduleRunDurationText
} from '../utils/agentSchedule'

withDefaults(defineProps<{
  runs?: AgentScheduleRunItem[]
  loading?: boolean
}>(), {
  runs: () => [],
  loading: false
})
</script>

<template>
  <el-table :data="runs" v-loading="loading" size="small" style="width: 100%" empty-text="暂无执行记录">
    <el-table-column label="状态" width="80">
      <template #default="{ row }">
        <span :class="['inline-block rounded px-1.5 py-0.5 text-[11px]', getScheduleRunStatusClass(row.status)]">
          {{ getScheduleRunStatusText(row.status) }}
        </span>
      </template>
    </el-table-column>
    <el-table-column label="来源" width="70">
      <template #default="{ row }">
        <span
          v-if="row.trigger_type === 'manual'"
          class="inline-block rounded bg-violet-50 px-1.5 py-0.5 text-[11px] text-violet-600"
        >手动</span>
        <span v-else class="text-xs text-slate-500">计划</span>
      </template>
    </el-table-column>
    <el-table-column label="计划时间" width="160">
      <template #default="{ row }">
        <span class="text-xs text-slate-600">{{ row.plan_time ? formatDateTimeValue(row.plan_time) : '-' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="完成时间" width="160">
      <template #default="{ row }">
        <span class="text-xs text-slate-600">{{ row.finished_time ? formatDateTimeValue(row.finished_time) : '-' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="耗时" width="80" align="right">
      <template #default="{ row }">
        <span class="text-xs text-slate-500">{{ scheduleRunDurationText(row) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="Agent Run" width="100">
      <template #default="{ row }">
        <span
          v-if="row.run_status"
          :class="['inline-block rounded px-1.5 py-0.5 text-[11px]', getScheduleRunStatusClass(row.run_status)]"
        >{{ getScheduleRunStatusText(row.run_status) }}</span>
        <span v-else class="text-xs text-slate-400">-</span>
      </template>
    </el-table-column>
    <el-table-column label="错误信息" min-width="200" show-overflow-tooltip>
      <template #default="{ row }">
        <span class="text-xs text-red-500">{{ row.error_message || '-' }}</span>
      </template>
    </el-table-column>
  </el-table>
</template>
