<script setup lang="ts">
/**
 * 定时任务执行历史表格（含分页）。
 * 后台管理面板与用户侧管理弹窗共用，保持字段口径与展示一致；
 * 分页状态与取数由父级持有，本组件只转发翻页/改页大小事件。
 */
import type { PropType } from 'vue'
import type { AgentScheduleRunItem } from '../types'
import { formatDateTimeValue } from '../utils'
import {
  getScheduleRunStatusClass,
  getScheduleRunStatusText,
  scheduleRunDurationText
} from '../utils/agentSchedule'

// Props 用运行时声明 + PropType：ChatData 会嵌入未安装 typescript 的宿主项目，
// 外部 import type 直接用于 defineProps<T>() 会导致编译失败（见 AGENTS 4.3）
defineProps({
  runs: { type: Array as PropType<AgentScheduleRunItem[]>, default: () => [] },
  loading: { type: Boolean, default: false },
  total: { type: Number, default: 0 },
  currentPage: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 }
})

const emit = defineEmits<{
  copyConversationId: [conversationId: string]
  pageChange: [page: number]
  sizeChange: [size: number]
}>()
</script>

<template>
  <div class="flex flex-col">
    <el-table :data="runs" v-loading="loading" size="small" style="width: 100%" max-height="60vh" empty-text="暂无执行记录">
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
      <el-table-column label="会话" min-width="120" show-overflow-tooltip>
        <template #default="{ row }">
          <span
            v-if="row.conversation_id"
            class="cursor-pointer font-mono text-xs text-slate-500"
            :title="`${row.conversation_id}（点击复制）`"
            @click="emit('copyConversationId', row.conversation_id)"
          >{{ row.conversation_id.slice(0, 8) }}…</span>
          <span v-else class="text-xs text-slate-400">-</span>
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
    <div class="flex shrink-0 justify-end pt-2">
      <el-pagination
        :current-page="currentPage"
        :page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next"
        size="small"
        background
        @current-change="(page: number) => emit('pageChange', page)"
        @size-change="(size: number) => emit('sizeChange', size)"
      />
    </div>
  </div>
</template>
