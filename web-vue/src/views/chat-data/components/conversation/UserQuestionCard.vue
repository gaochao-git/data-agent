<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { PropType } from 'vue'
import type { ApprovalItem, PendingUserQuestion, UserQuestionAnswer } from '../../types'
import { getToolDisplayName } from '../../utils/toolDisplay'

const props = defineProps({
  question: { type: Object as PropType<PendingUserQuestion>, required: true },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits<{
  submit: [answers: UserQuestionAnswer[]]
  cancel: []
}>()

const selected = reactive<Record<string, string[]>>({})
const custom = reactive<Record<string, string>>({})
const skipped = reactive<Record<string, boolean>>({})
const currentIndex = ref(0)

const currentQuestion = computed(() => props.question.questions[currentIndex.value])
const currentQuestionId = computed(() => currentQuestion.value?.id || '')
const totalQuestions = computed(() => props.question.questions.length)
const isLastQuestion = computed(() => currentIndex.value === totalQuestions.value - 1)
const currentSelected = computed(() => selected[currentQuestionId.value] || [])
const currentCustom = computed(() => (custom[currentQuestionId.value] || '').trim())
const currentSkipped = computed(() => skipped[currentQuestionId.value] === true)
const currentCanContinue = computed(() => currentSkipped.value || currentSelected.value.length > 0 || Boolean(currentCustom.value))

// ---- 批量审批：多操作逐条选择允许/拒绝（单审批仍走原单选卡） ----
const approvalItems = computed(() => (props.question.kind === 'approval' ? props.question.approval_items || [] : []))
const isBatchApproval = computed(
  () => approvalItems.value.length > 1 && approvalItems.value.length === props.question.questions.length
)
const batchAllDecided = computed(() =>
  approvalItems.value.every(item => (selected[item.call_id] || []).length > 0)
)
const batchDecidedCount = computed(
  () => approvalItems.value.filter(item => (selected[item.call_id] || []).length > 0).length
)
// 批量问题每项最多四态：[允许, 本轮内始终允许, 本会话内始终允许, 拒绝]；文案从
// 后端载荷读取，避免硬编码漂移。兼容旧载荷：三态（无本轮）与两态（仅允许/拒绝）。
const batchOptions = computed(() => currentQuestion.value?.options || [])
const batchAllowLabel = computed(() => batchOptions.value[0]?.label || '允许')
const batchRunAlwaysLabel = computed(() =>
  batchOptions.value.length >= 4 ? batchOptions.value[1]?.label || '本轮内始终允许' : ''
)
// 展示口径统一为"本会话内始终允许"；但提交值保留载荷原始 label，
// 以兼容升级前已下发、仍带旧文案的 pending 卡片（后端解析兼容旧 label）。
const LEGACY_ALWAYS_LABELS = new Set(['本次会话始终允许', '本对话内始终允许', '本窗口内始终允许'])
const displayApprovalLabel = (label: string) =>
  LEGACY_ALWAYS_LABELS.has(label) ? '本会话内始终允许' : label
const batchAlwaysRawLabel = computed(() => {
  const options = batchOptions.value
  if (options.length >= 4) return options[2]?.label || '本会话内始终允许'
  return options.length === 3 ? options[1]?.label || '本会话内始终允许' : ''
})
const batchAlwaysLabel = computed(() => displayApprovalLabel(batchAlwaysRawLabel.value))
const batchDenyLabel = computed(() => {
  const options = batchOptions.value
  const deny = options.length >= 4 ? options[3] : options.length === 3 ? options[2] : options[1]
  return deny?.label || '拒绝'
})
// 单工具审批也带 approval_items：在单选卡上方展示参数，避免"盲批"
const singleApprovalItem = computed(() => (approvalItems.value.length === 1 ? approvalItems.value[0] : null))

// ---- 审批卡展示口径（与全站一致，避免审批卡成为唯一的"技术视角"出口） ----
// 工具名：后端下发的 label 若是 MCP display_name 就照用；否则回落工具卡的中文显示名，
// 不能把 file_delete 这种内部名直接摆给用户。
const approvalDisplayName = (item: ApprovalItem) => {
  const backendLabel = String(item.label || '').trim()
  if (backendLabel && backendLabel !== item.tool_name) return backendLabel
  const mapped = getToolDisplayName(item.tool_name)
  return mapped === '工具调用' ? item.tool_name : mapped
}
// 参数里的沙箱绝对路径剥掉 /mnt/user-data 前缀，与工具卡 normalizeVisiblePath 同一口径；
// 按值判断而不是按参数名白名单——不同工具的路径键叫 file_path / dir_path / target 都有，
// 而 /mnt/user-data 是内部挂载点，出现在任何参数值里都不该原样展示。
const SANDBOX_PATH_PREFIX = /\/mnt\/user-data\//g
const visibleArgValue = (value: string) => (
  value.includes('/mnt/user-data/') ? value.replace(SANDBOX_PATH_PREFIX, '') : value
)
const argsEntries = (item: ApprovalItem) => Object.entries(item.args_summary || {})
const argsLine = (item: ApprovalItem) => argsEntries(item)
  .map(([key, value]) => `${key}: ${visibleArgValue(value)}`)
  .join(' · ')
const argsJson = (item: ApprovalItem) => JSON.stringify(
  Object.fromEntries(argsEntries(item).map(([key, value]) => [key, visibleArgValue(value)])),
  null,
  2,
)
// 审批标题由前端按显示名生成：后端只保证 tool_name 稳定，中文名的事实源在前端 toolDisplay
const questionHeading = computed(() => {
  const item = singleApprovalItem.value
  if (props.question.kind !== 'approval' || !item) return currentQuestion.value?.question || ''
  const name = approvalDisplayName(item)
  return item.tool_name.startsWith('mcp_') ? `是否允许执行 MCP 工具「${name}」？` : `是否允许执行「${name}」？`
})
const batchHint = computed(() => {
  const count = approvalItems.value.length
  if (batchRunAlwaysLabel.value) {
    return `共 ${count} 个操作，可逐项选择允许、本轮内始终允许、本会话内始终允许或拒绝`
  }
  return batchAlwaysLabel.value
    ? `共 ${count} 个操作，可逐项选择允许、本会话内始终允许或拒绝`
    : `共 ${count} 个操作，请逐项选择允许或拒绝`
})
// 「本会话内始终允许」的作用域必须写在悬浮说明里：批量卡不渲染 option 的 description，
// 只靠按钮文案，用户会误以为只对当前这批操作生效。
const alwaysAllowHint = (item: ApprovalItem) =>
  `本会话内不再询问「${approvalDisplayName(item)}」，后续同类操作直接执行；同名操作自动选中`
const batchSelectAll = (label: string) => {
  approvalItems.value.forEach(item => { selected[item.call_id] = [label] })
}
// 本轮/本窗口许可是「工具级」：选中后同名工具的其余行自动跟随；
// 「允许/拒绝」是「本次操作级」，只改当前行。
const toggleBatchToolLevel = (item: ApprovalItem, label: string) => {
  approvalItems.value
    .filter(candidate => candidate.tool_name === item.tool_name)
    .forEach(candidate => { selected[candidate.call_id] = [label] })
}
// 同名工具一旦选择本轮/本会话许可，"允许"被工具级许可覆盖，只允许把个别行改成"拒绝"
const toolLevelActive = (toolName: string) => approvalItems.value.some(candidate => {
  if (candidate.tool_name !== toolName) return false
  const value = (selected[candidate.call_id] || [])[0] || ''
  return value === batchRunAlwaysLabel.value || value === batchAlwaysRawLabel.value
})
const approvalIcon = (toolName: string) => {
  if (toolName.includes('dir_delete')) return 'fa-folder-minus text-red-500'
  if (toolName.includes('delete')) return 'fa-trash-can text-red-500'
  if (toolName.includes('move')) return 'fa-arrows-turn-right text-amber-500'
  if (toolName.startsWith('mcp_')) return 'fa-plug text-purple-500'
  return 'fa-screwdriver-wrench text-slate-500'
}

watch(() => props.question.question_id, () => {
  currentIndex.value = 0
  Object.keys(selected).forEach(key => delete selected[key])
  Object.keys(custom).forEach(key => delete custom[key])
  Object.keys(skipped).forEach(key => delete skipped[key])
})

const toggleOption = (questionId: string, label: string, multiSelect: boolean) => {
  skipped[questionId] = false
  const values = selected[questionId] || []
  selected[questionId] = multiSelect
    ? values.includes(label) ? values.filter(value => value !== label) : [...values, label]
    : [label]
}

const answers = computed<UserQuestionAnswer[]>(() => props.question.questions.map(item => ({
  id: item.id,
  selected: selected[item.id] || [],
  custom: (custom[item.id] || '').trim(),
  skipped: skipped[item.id] === true,
})))

const goPrevious = () => {
  if (currentIndex.value > 0 && !props.submitting) currentIndex.value -= 1
}

const goNext = () => {
  if (props.submitting) return
  if (isBatchApproval.value) {
    if (!batchAllDecided.value) return
    emit('submit', answers.value)
    return
  }
  if (!currentCanContinue.value) return
  if (!isLastQuestion.value) currentIndex.value += 1
  else emit('submit', answers.value)
}

const skipCurrent = () => {
  if (props.submitting || !currentQuestion.value) return
  skipped[currentQuestion.value.id] = true
  selected[currentQuestion.value.id] = []
  custom[currentQuestion.value.id] = ''
  if (!isLastQuestion.value) currentIndex.value += 1
  else emit('submit', answers.value)
}
</script>

<template>
  <div class="mb-3 overflow-hidden rounded-lg border border-blue-200 bg-white shadow-sm">
    <div class="border-b border-blue-100 bg-blue-50 px-4 py-3">
      <div class="flex items-center gap-2 text-sm font-semibold text-slate-800">
        <i class="fa-regular fa-circle-question text-blue-600"></i>
        需要你的确认
      </div>
    </div>
    <div v-if="currentQuestion" class="p-4">
      <!-- 批量审批：逐条选择允许/拒绝（否掉其中某个操作，其余仍可执行） -->
      <template v-if="isBatchApproval">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="text-sm text-slate-600">{{ batchHint }}</div>
          <div class="flex items-center gap-2 text-xs">
            <button type="button" class="h-7 rounded border border-slate-200 px-2.5 text-slate-600 hover:border-emerald-400" :disabled="submitting" title="把全部操作设为「允许」，仍可逐条改回" @click="batchSelectAll(batchAllowLabel)">全部允许</button>
            <button type="button" class="h-7 rounded border border-slate-200 px-2.5 text-slate-600 hover:border-red-400" :disabled="submitting" title="把全部操作设为「拒绝」" @click="batchSelectAll(batchDenyLabel)">全部拒绝</button>
          </div>
        </div>
        <div class="mt-3 max-h-[320px] space-y-2 overflow-y-auto pr-1">
          <div
            v-for="item in approvalItems"
            :key="item.call_id"
            class="rounded-md border px-3 py-2 transition-colors"
            :class="(selected[item.call_id] || []).length ? 'border-blue-300 bg-blue-50/30' : 'border-slate-200'"
          >
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2 text-sm font-medium text-slate-800">
                  <i class="fa-solid text-xs" :class="approvalIcon(item.tool_name)"></i>
                  <span class="truncate">{{ approvalDisplayName(item) }}</span>
                  <span v-if="item.intent" class="truncate text-xs font-normal text-slate-500">{{ item.intent }}</span>
                </div>
                <div v-if="argsEntries(item).length" class="mt-1 min-w-0">
                  <div class="approval-args-line truncate whitespace-nowrap font-mono text-[11px] text-slate-500" :title="argsJson(item)">
                    {{ argsLine(item) }}
                  </div>
                </div>
              </div>
              <div class="ml-auto flex shrink-0 flex-nowrap items-center justify-end gap-1">
                <button
                  type="button"
                  class="h-7 rounded px-1.5 text-[11px]"
                  :class="[
                    (selected[item.call_id] || []).includes(batchAllowLabel) ? 'bg-emerald-600 text-white' : 'border border-slate-200 text-slate-600 hover:border-emerald-400',
                    toolLevelActive(item.tool_name) ? 'cursor-not-allowed opacity-40' : ''
                  ]"
                  :disabled="submitting || toolLevelActive(item.tool_name)"
                  :title="toolLevelActive(item.tool_name) ? '该工具已选择本轮/本会话始终允许，「允许」已被工具级许可覆盖；如需排除某条请用「拒绝」' : '允许该工具以当前参数执行这一次（仅本次调用，不产生后续授权）'"
                  @click="toggleOption(item.call_id, batchAllowLabel, false)"
                >{{ batchAllowLabel }}</button>
                <button
                  v-if="batchRunAlwaysLabel"
                  type="button"
                  class="h-7 rounded px-1.5 text-[11px]"
                  :class="(selected[item.call_id] || []).includes(batchRunAlwaysLabel) ? 'bg-indigo-600 text-white' : 'border border-slate-200 text-slate-600 hover:border-indigo-400'"
                  :disabled="submitting"
                  title="本次回答内不再询问该工具；同名操作自动选中"
                  @click="toggleBatchToolLevel(item, batchRunAlwaysLabel)"
                >{{ batchRunAlwaysLabel }}</button>
                <button
                  v-if="batchAlwaysLabel"
                  type="button"
                  class="h-7 rounded px-1.5 text-[11px]"
                  :class="(selected[item.call_id] || []).includes(batchAlwaysRawLabel) ? 'bg-blue-600 text-white' : 'border border-slate-200 text-slate-600 hover:border-blue-400'"
                  :disabled="submitting"
                  :title="alwaysAllowHint(item)"
                  @click="toggleBatchToolLevel(item, batchAlwaysRawLabel)"
                >{{ batchAlwaysLabel }}</button>
                <button
                  type="button"
                  class="h-7 rounded px-1.5 text-[11px]"
                  :class="(selected[item.call_id] || []).includes(batchDenyLabel) ? 'bg-red-500 text-white' : 'border border-slate-200 text-slate-600 hover:border-red-400'"
                  :disabled="submitting"
                  title="不执行；智能体会收到拒绝原因并调整方案"
                  @click="toggleOption(item.call_id, batchDenyLabel, false)"
                >{{ batchDenyLabel }}</button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="batchAlwaysLabel || batchRunAlwaysLabel" class="mt-2 text-[11px] leading-5 text-slate-500">
          <template v-if="batchRunAlwaysLabel">「{{ batchRunAlwaysLabel }}」= 本次回答内该工具不再询问。</template>
          <template v-if="batchAlwaysLabel">「{{ batchAlwaysLabel }}」= 整个会话内该工具不再询问。</template>
        </div>
        <div v-if="batchAlwaysLabel || batchRunAlwaysLabel" class="mt-1 text-[11px] leading-5 text-slate-400">
          工具级选项会自动选中同名工具的其他操作；之后「允许」由工具级许可覆盖，只想排除某一条时单独点该行的「拒绝」，只影响本次操作。
        </div>
        <div class="mt-5 flex items-center justify-end gap-2">
          <button type="button" class="h-8 px-3 text-sm text-slate-500 hover:text-slate-700" :disabled="submitting" @click="emit('cancel')">取消</button>
          <button
            type="button"
            class="h-8 rounded-md bg-slate-900 px-4 text-sm text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!batchAllDecided || submitting"
            @click="goNext"
          >
            <i v-if="submitting" class="fa-solid fa-spinner fa-spin mr-1"></i>
            提交（{{ batchDecidedCount }}/{{ approvalItems.length }}）
          </button>
        </div>
      </template>
      <!-- 单问题（含单工具审批）：保持原单选卡与分页交互 -->
      <template v-else>
        <div>
          <div v-if="currentQuestion.header" class="mb-2 text-xs font-medium text-slate-500">{{ currentQuestion.header }}</div>
          <!-- 单工具审批：工具名与参数摘要同一行，悬浮显示完整参数 JSON；减少单工具卡高度 -->
          <div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span class="text-base font-semibold text-slate-900">{{ questionHeading }}</span>
            <span
              v-if="singleApprovalItem && argsEntries(singleApprovalItem).length"
              class="approval-args-line min-w-0 truncate font-mono text-[11px] text-slate-400"
              :title="argsJson(singleApprovalItem)"
            >{{ argsLine(singleApprovalItem) }}</span>
            <span v-if="singleApprovalItem?.intent" class="text-xs font-normal text-slate-500">{{ singleApprovalItem.intent }}</span>
          </div>
          <div v-if="currentQuestion.options.length" class="mt-3 max-h-[240px] space-y-1.5 overflow-y-auto pr-1">
            <button
              v-for="option in currentQuestion.options"
              :key="option.label"
              type="button"
              class="flex w-full items-center gap-2 rounded-md border px-3 py-1.5 text-left transition-colors"
              :class="currentSelected.includes(option.label) ? 'border-blue-500 bg-blue-50' : 'border-slate-200 hover:border-blue-300'"
              :disabled="submitting"
              @click="toggleOption(currentQuestion.id, option.label, currentQuestion.multi_select)"
            >
              <i
                class="text-sm"
                :class="[
                  currentQuestion.multi_select ? 'fa-regular fa-square-check' : 'fa-regular fa-circle-dot',
                  currentSelected.includes(option.label) ? 'text-blue-600' : 'text-slate-300',
                ]"
              ></i>
              <span class="flex min-w-0 flex-1 items-baseline gap-2">
                <span class="shrink-0 text-sm text-slate-700">{{ displayApprovalLabel(option.label) }}</span>
                <span
                  v-if="option.description"
                  class="min-w-0 truncate text-xs text-slate-400"
                  :title="option.description"
                >{{ option.description }}</span>
              </span>
            </button>
          </div>
          <!-- 审批不接受自由文本：后端只读 selected，写了也不会被采纳（空选择=拒绝），
               与其让人以为"说明理由即可放行"，不如不给这个输入框 -->
          <textarea
            v-if="question.kind !== 'approval'"
            v-model="custom[currentQuestion.id]"
            rows="2"
            class="mt-2 w-full resize-none rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-500"
            :placeholder="currentQuestion.options.length ? '补充说明（可选）' : '请输入你的回答'"
            :disabled="submitting"
          ></textarea>
        </div>
        <div class="mt-5 flex items-center justify-between gap-3">
          <div class="flex items-center gap-3 text-sm text-slate-500">
            <button type="button" class="p-1 text-lg hover:text-slate-800 disabled:opacity-30" :disabled="currentIndex === 0 || submitting" title="上一题" @click="goPrevious">
              <i class="fa-solid fa-chevron-left"></i>
            </button>
            <span class="min-w-[42px] text-center">{{ currentIndex + 1 }}/{{ totalQuestions }}</span>
            <button type="button" class="p-1 text-lg hover:text-slate-800 disabled:opacity-30" :disabled="isLastQuestion || submitting" title="下一题" @click="goNext">
              <i class="fa-solid fa-chevron-right"></i>
            </button>
          </div>
          <div class="flex items-center gap-2">
            <!-- 「跳过」在审批语义里等于静默拒绝，和「取消」重复且更容易误点，审批卡不提供 -->
            <button v-if="question.kind !== 'approval'" type="button" class="h-8 px-3 text-sm text-slate-600 hover:text-slate-900" :disabled="submitting" @click="skipCurrent">跳过本题</button>
            <button type="button" class="h-8 px-3 text-sm text-slate-500 hover:text-slate-700" :disabled="submitting" @click="emit('cancel')">取消</button>
            <button
              type="button"
              class="h-8 rounded-md bg-slate-900 px-4 text-sm text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!currentCanContinue || submitting"
              @click="goNext"
            >
              <i v-if="submitting" class="fa-solid fa-spinner fa-spin mr-1"></i>
              {{ isLastQuestion ? '提交' : '下一题' }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
