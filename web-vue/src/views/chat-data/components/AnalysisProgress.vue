<script setup lang="ts">
/**
 * 分析计划进度条：常驻输入框上方，展示 write_todos 管理的当前分析任务。
 * 一旦出现即常驻本会话：不转圈、不主动消失，用分段计数实时反映清单状态。
 */
import { computed, ref } from 'vue'
import type { AnalysisTodosState } from '../utils/analysisTodos'

const props = defineProps<{ todos: AnalysisTodosState }>()

const collapsed = ref(true)

const completedCount = computed(() => props.todos.items.filter(item => item.status === 'completed').length)
const inProgressCount = computed(() => props.todos.items.filter(item => item.status === 'in_progress').length)
const pendingCount = computed(() => props.todos.items.filter(item => item.status === 'pending').length)
// 全部完成只留汇总；未完成时展示已完成/进行中/待处理三段计数
const statusText = computed(() => {
  if (props.todos.finished) return `${completedCount.value} 已完成`
  return [
    `${completedCount.value} 已完成`,
    `${inProgressCount.value} 进行中`,
    `${pendingCount.value} 待处理`,
  ].join(' · ')
})
</script>

<template>
  <div class="overflow-hidden rounded-lg border border-slate-200 bg-white">
    <button
      type="button"
      class="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors hover:bg-slate-50"
      @click="collapsed = !collapsed"
    >
      <i class="fa-solid fa-list-check shrink-0 text-xs text-slate-500"></i>
      <span class="font-medium text-slate-700">任务</span>
      <span class="text-slate-500">{{ statusText }}</span>
      <i class="fa-solid ml-auto shrink-0 text-[10px] text-slate-400" :class="collapsed ? 'fa-chevron-down' : 'fa-chevron-up'"></i>
    </button>
    <div v-if="!collapsed" class="max-h-[180px] overflow-y-auto border-t border-slate-100 px-3 py-2">
      <div class="flex flex-col gap-1.5">
        <div v-for="(item, index) in todos.items" :key="index" class="flex items-center gap-2 text-[13px]">
          <i v-if="item.status === 'completed'" class="fa-solid fa-circle-check shrink-0 text-xs text-emerald-500"></i>
          <i v-else-if="item.status === 'in_progress'" class="fa-solid fa-spinner fa-spin shrink-0 text-xs text-[#1677FF]"></i>
          <span v-else class="h-3 w-3 shrink-0 rounded-full border border-dashed border-slate-300"></span>
          <span :class="[
            item.status === 'completed' ? 'text-slate-400 line-through' : '',
            item.status === 'in_progress' ? 'font-medium text-slate-800' : '',
            item.status === 'pending' ? 'text-slate-500' : '',
          ]" class="min-w-0 truncate">{{ item.content }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
