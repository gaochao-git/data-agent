<script setup lang="ts">
/**
 * 语义模型校验结果弹窗。
 * 展示数据源两级健康检查结果：① 物理库 ↔ 缓存表（表结构漂移）；
 * ② 缓存表 ↔ 语义模型（指标/维度/关联引用的表字段是否仍存在）。
 * 数据由调用方请求 getAdminDatasourceSemanticHealth 后传入，本组件纯展示。
 */
import type { DatasourceSemanticHealthResult } from '../../api'

defineProps<{
  result: DatasourceSemanticHealthResult | null
}>()

const visible = defineModel<boolean>({ default: false })

const getHealthStatusLabel = (status?: string) => {
  return status === 'ok' ? '正常' : '有问题'
}

const getHealthStatusBadgeClass = (status?: string) => {
  return status === 'ok' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'
}
</script>

<template>
  <el-dialog v-model="visible" width="860px" :show-close="false">
    <template #header>
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-shield-halved text-blue-600"></i>
          <h3 class="m-0 text-lg font-semibold text-slate-900">语义模型校验<span v-if="result"> - {{ result.group_name }}</span></h3>
        </div>
        <button class="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600" @click="visible = false">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </template>

    <div v-if="result" class="max-h-[72vh] space-y-4 overflow-y-auto pr-1">
      <div class="rounded-xl border border-slate-200 bg-white">
        <div class="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <div class="flex items-start gap-3">
            <div class="mt-0.5 flex h-7 w-7 items-center justify-center rounded-full bg-blue-50 text-xs font-semibold text-blue-700">1</div>
            <div>
              <div class="text-sm font-semibold text-slate-900">物理库 ↔ 缓存表</div>
              <div class="mt-0.5 text-xs text-slate-400">检查数据源缓存中的表和字段，在真实物理库中是否仍存在、类型是否变化。</div>
            </div>
          </div>
          <span
            :class="['rounded-full px-2 py-0.5 text-xs font-medium', getHealthStatusBadgeClass(result.schema?.status)]"
          >
            {{ getHealthStatusLabel(result.schema?.status) }}
          </span>
        </div>
        <div class="border-b border-slate-100 px-4 py-3 text-xs text-slate-500">
          已校验 {{ result.schema?.summary.cached_table_count || 0 }} 张缓存表，发现 {{ result.schema?.summary.issue_count || 0 }} 个问题
        </div>
        <div v-if="!result.schema || result.schema.issues.length === 0" class="border-t border-slate-100 px-4 py-3 text-xs text-slate-400">
          缓存表和字段在物理库中均存在，未发现表结构漂移。
        </div>
        <div v-else class="space-y-2 border-t border-slate-100 p-3">
          <div
            v-for="(issue, index) in result.schema.issues"
            :key="`schema-${index}`"
            class="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700"
          >
            {{ issue.message }}
          </div>
        </div>
      </div>

      <div v-if="result.configs.length === 0" class="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-400">
        当前数据源还没有语义模型
      </div>
      <div v-else class="rounded-xl border border-slate-200 bg-white">
        <div class="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div class="flex items-start gap-3">
            <div class="mt-0.5 flex h-7 w-7 items-center justify-center rounded-full bg-blue-50 text-xs font-semibold text-blue-700">2</div>
            <div>
              <div class="text-sm font-semibold text-slate-900">缓存表 ↔ 语义模型</div>
              <div class="mt-0.5 text-xs text-slate-400">检查指标、维度、关联关系引用的表字段，是否仍存在于当前缓存表结构。</div>
            </div>
          </div>
          <span
            :class="['rounded-full px-2 py-0.5 text-xs font-medium', getHealthStatusBadgeClass(result.semantic?.status || result.status)]"
          >
            {{ getHealthStatusLabel(result.semantic?.status || result.status) }}
          </span>
        </div>
        <div class="border-b border-slate-100 px-4 py-3 text-xs text-slate-500">
          已校验 {{ result.semantic?.summary.config_count ?? result.summary.config_count }} 个语义模型，发现 {{ result.semantic?.summary.issue_count ?? result.summary.issue_count }} 个问题
        </div>
        <div class="max-h-[360px] space-y-3 overflow-y-auto bg-slate-50/40 p-3">
          <div v-for="config in result.configs" :key="config.config_id" class="rounded-xl border border-slate-200 bg-white">
            <div class="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
              <div class="min-w-0 flex-1">
                <div class="truncate text-sm font-semibold text-slate-900">{{ config.config_name || config.config_id }}</div>
                <div class="mt-0.5 text-xs text-slate-400">
                  {{ config.issues.length === 0 ? '未发现异常' : `${config.issues.length} 项需处理` }}
                </div>
              </div>
              <span
                :class="['rounded-full px-2 py-0.5 text-xs font-medium', getHealthStatusBadgeClass(config.status)]"
              >
                {{ getHealthStatusLabel(config.status) }}
              </span>
            </div>
            <div v-if="config.issues.length === 0" class="px-4 py-3 text-xs text-slate-400">
              该语义模型引用的指标、维度和关联关系均能匹配当前缓存表结构。
            </div>
            <div v-else class="space-y-2 p-3">
              <div
                v-for="(issue, index) in config.issues"
                :key="`${config.config_id}-${index}`"
                class="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700"
              >
                {{ issue.message }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </el-dialog>
</template>
