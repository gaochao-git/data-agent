<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { PropType } from 'vue'
import type { PendingUserQuestion, UserQuestionAnswer } from '../types'

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
  if (!currentCanContinue.value || props.submitting) return
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
      <div class="min-h-[220px]">
        <div v-if="currentQuestion.header" class="mb-2 text-xs font-medium text-slate-500">{{ currentQuestion.header }}</div>
        <div class="text-base font-semibold text-slate-900">{{ currentQuestion.question }}</div>
        <div v-if="currentQuestion.options.length" class="mt-5 max-h-[240px] space-y-2 overflow-y-auto pr-1">
          <button
            v-for="option in currentQuestion.options"
            :key="option.label"
            type="button"
            class="flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left transition-colors"
            :class="currentSelected.includes(option.label) ? 'border-blue-500 bg-blue-50' : 'border-slate-200 hover:border-blue-300'"
            :disabled="submitting"
            @click="toggleOption(currentQuestion.id, option.label, currentQuestion.multi_select)"
          >
            <i
              class="mt-0.5 text-sm"
              :class="[
                currentQuestion.multi_select ? 'fa-regular fa-square-check' : 'fa-regular fa-circle-dot',
                currentSelected.includes(option.label) ? 'text-blue-600' : 'text-slate-300',
              ]"
            ></i>
            <span class="min-w-0">
              <span class="block text-sm text-slate-700">{{ option.label }}</span>
              <span v-if="option.description" class="mt-0.5 block text-xs text-slate-500">{{ option.description }}</span>
            </span>
          </button>
        </div>
        <textarea
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
          <button type="button" class="h-8 px-3 text-sm text-slate-600 hover:text-slate-900" :disabled="submitting" @click="skipCurrent">跳过本题</button>
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
    </div>
  </div>
</template>
