<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { learnSemanticConfigFromHistory, type SemanticHistoryLearningResult, type SemanticHistoryLearningSuggestion } from '../../api'
import type { SemanticConfigItem } from '../../types'

const props = defineProps<{
  datasourceId: string
  config: SemanticConfigItem | null
  adminMode?: boolean
}>()

const emit = defineEmits<{
  acceptMetric: [suggestion: SemanticHistoryLearningSuggestion]
  acceptDimension: [suggestion: SemanticHistoryLearningSuggestion]
}>()

const visible = defineModel<boolean>('visible', { default: false })
const historyLearningLoading = ref(false)
const historyLearningForm = ref({
  days: 7,
  limit: 50,
  sources: ['agent', 'builder', 'sql'] as Array<'agent' | 'builder' | 'sql'>,
  include_failed: true,
})
const historyLearningResult = ref<SemanticHistoryLearningResult | null>(null)

const resetHistoryLearningState = () => {
  historyLearningResult.value = null
  historyLearningForm.value = {
    days: 7,
    limit: 50,
    sources: ['agent', 'builder', 'sql'],
    include_failed: true,
  }
}

const runHistoryLearning = async () => {
  if (!props.config) return
  if (historyLearningForm.value.sources.length === 0) {
    ElMessage.warning('请至少选择一个学习来源')
    return
  }
  historyLearningLoading.value = true
  try {
    const res = await learnSemanticConfigFromHistory(props.datasourceId, props.config.id, {
      days: historyLearningForm.value.days,
      limit: historyLearningForm.value.limit,
      sources: historyLearningForm.value.sources,
      include_failed: historyLearningForm.value.include_failed,
    }, { adminMode: Boolean(props.adminMode) })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '历史学习失败')
      return
    }
    historyLearningResult.value = res.data
    ElMessage.success('历史学习完成')
  } catch (err) {
    const apiMsg = (err as any)?.response?.data?.msg
    ElMessage.error(apiMsg || '历史学习失败')
  } finally {
    historyLearningLoading.value = false
  }
}

const getHistorySampleIntent = (sample: { query_text?: string; conversation_context?: { user_turns?: Array<{ content: string }> } }) => {
  const latestUserTurn = sample.conversation_context?.user_turns?.slice(-1)[0]?.content
  return latestUserTurn || sample.query_text || '-'
}

const getHistorySampleSignal = (sample: { error_message?: string; sql?: string; conversation_context?: { negative_feedback?: string[]; tool_errors?: Array<{ content: string }> } }) => {
  const feedback = sample.conversation_context?.negative_feedback?.slice(-1)[0]
  if (feedback) return `用户反馈：${feedback}`
  const toolError = sample.conversation_context?.tool_errors?.slice(-1)[0]?.content
  if (toolError) return `工具错误：${toolError}`
  return sample.error_message || sample.sql || '-'
}

const getHistorySampleSignalType = (sample: { error_message?: string; conversation_context?: { negative_feedback?: string[]; tool_errors?: Array<{ content: string }> } }) => {
  if (sample.conversation_context?.negative_feedback?.length) return '用户反馈'
  if (sample.conversation_context?.tool_errors?.length) return '工具错误'
  if (sample.error_message) return '执行错误'
  return 'SQL'
}

watch(visible, (next) => {
  if (next) resetHistoryLearningState()
})
</script>

<template>
  <el-dialog v-model="visible" width="1080px" title="历史学习">
    <div class="space-y-4">
      <div class="rounded-md border border-gray-200 bg-gray-50 px-3 py-3">
        <div class="grid grid-cols-[150px_150px_1fr_auto] gap-3 items-end">
          <div>
            <div class="text-xs text-gray-600 mb-1">最近天数</div>
            <el-input-number v-model="historyLearningForm.days" :min="1" :max="90" :step="1" size="small" />
          </div>
          <div>
            <div class="text-xs text-gray-600 mb-1">最大数量</div>
            <el-input-number v-model="historyLearningForm.limit" :min="1" :max="200" :step="10" size="small" />
          </div>
          <div>
            <div class="text-xs text-gray-600 mb-1">学习来源</div>
            <el-checkbox-group v-model="historyLearningForm.sources" size="small">
              <el-checkbox-button value="agent">智能问数</el-checkbox-button>
              <el-checkbox-button value="builder">可视化查询</el-checkbox-button>
              <el-checkbox-button value="sql">SQL查询</el-checkbox-button>
            </el-checkbox-group>
          </div>
          <div class="flex items-center gap-3">
            <el-checkbox v-model="historyLearningForm.include_failed">包含失败记录</el-checkbox>
            <el-button type="primary" :loading="historyLearningLoading" @click="runHistoryLearning">
              开始学习
            </el-button>
          </div>
        </div>
        <div class="mt-2 text-xs text-gray-500">
          智能问数学用户对话和纠错反馈；可视化查询学习用户配置的维度、指标和过滤；SQL查询学习用户编写的SQL。仅分析当前语义模型相关历史，采纳后仍需保存当前语义模型。
        </div>
        <div class="mt-1 text-xs text-gray-500">
          执行时先取最近 {{ historyLearningForm.days }} 天历史，最多扫描 {{ historyLearningForm.limit * 5 }} 条用于筛选，最终最多学习 {{ historyLearningForm.limit }} 条。
        </div>
      </div>

      <div v-if="historyLearningResult" class="grid grid-cols-6 gap-2 text-xs">
        <div class="rounded border border-gray-200 bg-white px-3 py-2" :title="`最近 ${historyLearningResult.days} 天内最多扫描 ${historyLearningResult.scan_limit || historyLearningResult.limit * 5} 条历史`">
          扫描历史 {{ historyLearningResult.scanned_count }} 条
        </div>
        <div class="rounded border border-gray-200 bg-white px-3 py-2">
          参与学习 {{ historyLearningResult.matched_count }} 条
        </div>
        <div class="rounded border border-gray-200 bg-white px-3 py-2">
          智能问数 {{ historyLearningResult.source_stats?.agent?.matched || 0 }} 条
        </div>
        <div class="rounded border border-gray-200 bg-white px-3 py-2">
          可视化查询 {{ historyLearningResult.source_stats?.builder?.matched || 0 }} 条
        </div>
        <div class="rounded border border-gray-200 bg-white px-3 py-2">
          SQL查询 {{ historyLearningResult.source_stats?.sql?.matched || 0 }} 条
        </div>
        <div class="rounded border border-gray-200 bg-white px-3 py-2" :title="`过滤原因包括：来源未选、非当前语义模型、SQL表范围不匹配、SQL解析失败、智能问数缺少对话上下文等`">
          过滤 {{ Math.max(0, historyLearningResult.scanned_count - historyLearningResult.matched_count) }} 条
        </div>
      </div>

      <el-tabs v-if="historyLearningResult" class="history-learning-tabs">
        <el-tab-pane label="候选指标">
          <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200">
            <table class="min-w-full border-collapse text-[12px]">
              <thead class="sticky top-0 bg-gray-50 text-gray-600">
                <tr>
                  <th class="border border-gray-200 px-3 py-2 text-left">名称</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">表/字段</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">表达式</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">原因</th>
                  <th class="border border-gray-200 px-3 py-2 text-center w-[90px]">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in historyLearningResult.suggestions.metrics" :key="`learn-metric-${item.name}-${item.table}`" class="hover:bg-gray-50">
                  <td class="border border-gray-200 px-3 py-2">{{ item.name }}</td>
                  <td class="border border-gray-200 px-3 py-2">{{ item.table || '-' }} / {{ item.field_name || '-' }}</td>
                  <td class="border border-gray-200 px-3 py-2 font-mono">{{ item.expression || '-' }}</td>
                  <td class="border border-gray-200 px-3 py-2">
                    <div>{{ item.reason }}</div>
                    <div v-if="item.needs_review" class="mt-1 text-red-500">{{ item.review_reason }}</div>
                  </td>
                  <td class="border border-gray-200 px-3 py-2 text-center">
                    <button class="px-2 py-1 rounded border border-blue-200 bg-white text-blue-600 hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed" :disabled="item.needs_review" @click="emit('acceptMetric', item)">采纳</button>
                  </td>
                </tr>
                <tr v-if="historyLearningResult.suggestions.metrics.length === 0">
                  <td colspan="5" class="border border-gray-200 px-3 py-8 text-center text-gray-400">暂无候选指标</td>
                </tr>
              </tbody>
            </table>
          </div>
        </el-tab-pane>
        <el-tab-pane label="候选维度">
          <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200">
            <table class="min-w-full border-collapse text-[12px]">
              <thead class="sticky top-0 bg-gray-50 text-gray-600">
                <tr>
                  <th class="border border-gray-200 px-3 py-2 text-left">名称</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">表/字段</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">类型</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">原因</th>
                  <th class="border border-gray-200 px-3 py-2 text-center w-[90px]">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in historyLearningResult.suggestions.dimensions" :key="`learn-dim-${item.name}-${item.table}`" class="hover:bg-gray-50">
                  <td class="border border-gray-200 px-3 py-2">{{ item.name }}</td>
                  <td class="border border-gray-200 px-3 py-2">{{ item.table || '-' }} / {{ item.column || item.field_name || '-' }}</td>
                  <td class="border border-gray-200 px-3 py-2">{{ item.logical_type || 'text' }}</td>
                  <td class="border border-gray-200 px-3 py-2">
                    <div>{{ item.reason }}</div>
                    <div v-if="item.needs_review" class="mt-1 text-red-500">{{ item.review_reason }}</div>
                  </td>
                  <td class="border border-gray-200 px-3 py-2 text-center">
                    <button class="px-2 py-1 rounded border border-blue-200 bg-white text-blue-600 hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed" :disabled="item.needs_review" @click="emit('acceptDimension', item)">采纳</button>
                  </td>
                </tr>
                <tr v-if="historyLearningResult.suggestions.dimensions.length === 0">
                  <td colspan="5" class="border border-gray-200 px-3 py-8 text-center text-gray-400">暂无候选维度</td>
                </tr>
              </tbody>
            </table>
          </div>
        </el-tab-pane>
        <el-tab-pane label="配置问题">
          <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200 p-2">
            <div v-for="item in historyLearningResult.suggestions.issues" :key="`learn-issue-${item.title}`" class="mb-2 rounded border border-amber-200 bg-amber-50 p-2">
              <div class="font-medium text-amber-800">{{ item.title }}</div>
              <div class="mt-1 whitespace-pre-wrap text-amber-700">{{ item.content }}</div>
              <div class="mt-1 text-amber-600">{{ item.reason }}</div>
            </div>
            <div v-if="historyLearningResult.suggestions.issues.length === 0" class="py-12 text-center text-xs text-gray-400">
              暂无配置问题
            </div>
          </div>
        </el-tab-pane>
        <el-tab-pane label="历史样例">
          <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200">
            <table class="min-w-full border-collapse text-[12px]">
              <thead class="sticky top-0 bg-gray-50 text-gray-600">
                <tr>
                  <th class="border border-gray-200 px-3 py-2 text-left">来源</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">状态</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">用户意图</th>
                  <th class="border border-gray-200 px-3 py-2 text-left">学习信号/SQL</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in historyLearningResult.samples" :key="item.execution_id" class="hover:bg-gray-50">
                  <td class="border border-gray-200 px-3 py-2">{{ item.source === 'agent' ? '智能问数' : item.source === 'builder' ? '可视化查询' : 'SQL查询' }}</td>
                  <td class="border border-gray-200 px-3 py-2">{{ item.status }}</td>
                  <td class="border border-gray-200 px-3 py-2 max-w-[320px]">
                    <div class="truncate" :title="getHistorySampleIntent(item)">{{ getHistorySampleIntent(item) }}</div>
                    <div v-if="item.conversation_id" class="mt-1 text-[11px] text-gray-400">会话 {{ item.conversation_id }}</div>
                  </td>
                  <td class="border border-gray-200 px-3 py-2 max-w-[520px]">
                    <div class="mb-1 text-[11px] text-gray-400">{{ getHistorySampleSignalType(item) }}</div>
                    <div class="truncate font-mono" :title="getHistorySampleSignal(item)">{{ getHistorySampleSignal(item) }}</div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
  </el-dialog>
</template>
