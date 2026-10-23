<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElPopover } from 'element-plus'

import ChatInputBox from '../ChatInputBox.vue'
import ModelPicker from '../ModelPicker.vue'

interface ModelThinkingOption {
  mode: 'none' | 'optional' | 'forced'
  efforts: string[]
  default_on: boolean
  default_effort: string | null
}

interface ModelOption {
  id: string
  label: string
  /** 思考能力声明（/config 下发）；缺省按 optional 处理。 */
  thinking?: ModelThinkingOption
}

interface Props {
  input: string
  placeholder: string
  /** 已选技能列表（title 展示、name 用于单项移除）。 */
  selectedSkills?: Array<{ name: string; title?: string }>
  /** 输入框 @ 引用的工作区文件列表（多选，chip 展示、单项可移除）。 */
  selectedMentionFiles?: Array<{ relativePath: string; name: string }>
  /** 划选助手消息「添加到对话」的引用片段（chip 展示 + hover 预览原文、单项可移除）。 */
  quotedSnippets?: QuotedSnippetChip[]
  selectedModel: string
  modelOptions: ModelOption[]
  /** 三态：true/false 用户显式选择；null 未触碰（展示回退模型 default_on）。 */
  enableDeepThinking: boolean | null
  /** 思考档位；null 未选择（展示回退模型 default_effort）。 */
  thinkingEffort: string | null
  isGenerating: boolean
  isStopping?: boolean
  canSubmit: boolean
  inputFocused?: boolean
  submitTitle?: string
}

/** 划选引用片段 chip（props 内联结构，供 chip 与预览卡渲染）。 */
interface QuotedSnippetChip {
  id: string
  text: string
}

const props = withDefaults(defineProps<Props>(), {
  selectedSkills: () => [],
  selectedMentionFiles: () => [],
  quotedSnippets: () => [],
  inputFocused: false,
  submitTitle: '发送 (Enter)',
})
const inputBoxRef = ref<InstanceType<typeof ChatInputBox> | null>(null)
const plusMenuRef = ref<HTMLElement | null>(null)
const plusMenuOpen = ref(false)

const emit = defineEmits<{
  submit: []
  stop: []
  clearSkill: [name: string]
  clearMentionFile: [relativePath: string]
  removeQuoteSnippet: [id: string]
  openSkillManager: []
  uploadFile: []
  pasteFiles: [files: File[]]
  keydown: [event: KeyboardEvent]
  focus: []
  blur: []
  'update:input': [value: string]
  'update:selectedModel': [value: string]
  'update:enableDeepThinking': [value: boolean]
  'update:thinkingEffort': [value: string]
}>()

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (plusMenuRef.value?.contains(target)) return
  plusMenuOpen.value = false
}

const focus = () => {
  inputBoxRef.value?.focus()
}

const resizeTextarea = () => {
  inputBoxRef.value?.resizeTextarea()
}

defineExpose({
  focus,
  resizeTextarea,
})

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<template>
  <ChatInputBox
    ref="inputBoxRef"
    :input="input"
    :placeholder="placeholder"
    :is-generating="isGenerating"
    :is-stopping="isStopping"
    :can-submit="canSubmit"
    :submit-title="submitTitle"
    :input-focused="inputFocused"
    @update:input="emit('update:input', $event)"
    @submit="emit('submit')"
    @stop="emit('stop')"
    @paste-files="emit('pasteFiles', $event)"
    @keydown="emit('keydown', $event)"
    @focus="emit('focus')"
    @blur="emit('blur')"
  >
    <template #prepend>
      <div
        v-if="props.selectedSkills.length || props.selectedMentionFiles.length || props.quotedSnippets.length"
        class="mb-2 flex flex-wrap items-center gap-1.5"
      >
        <div
          v-for="file in props.selectedMentionFiles"
          :key="file.relativePath"
          class="flex w-fit items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700"
          :title="`本轮引用该工作区文件（${file.relativePath}）`"
        >
          <i class="fa-solid fa-at text-[10px]"></i>
          <span class="max-w-[140px] truncate">@{{ file.name }}</span>
          <button type="button" class="text-blue-400 hover:text-blue-700" @click="emit('clearMentionFile', file.relativePath)">
            <i class="fa-solid fa-xmark text-[10px]"></i>
          </button>
        </div>
        <div
          v-for="snippet in props.quotedSnippets"
          :key="snippet.id"
          class="flex w-fit items-center gap-1.5 rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600"
        >
          <el-popover placement="top" :width="380" trigger="hover">
            <template #reference>
              <span class="flex min-w-0 cursor-default items-center gap-1.5" title="引用的助手回复片段">
                <i class="fa-solid fa-quote-left text-[10px] text-slate-400"></i>
                <span class="max-w-[140px] truncate">{{ snippet.text }}</span>
              </span>
            </template>
            <div class="max-h-[220px] overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-700">{{ snippet.text }}</div>
          </el-popover>
          <button type="button" class="text-slate-400 hover:text-slate-700" title="移除引用" @click="emit('removeQuoteSnippet', snippet.id)">
            <i class="fa-solid fa-xmark text-[10px]"></i>
          </button>
        </div>
        <div
          v-for="skill in props.selectedSkills"
          :key="skill.name"
          class="flex w-fit items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700"
        >
          <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
          <span class="max-w-[140px] truncate">{{ skill.title || skill.name }}</span>
          <button type="button" class="text-blue-400 hover:text-blue-700" @click="emit('clearSkill', skill.name)">
            <i class="fa-solid fa-xmark text-[10px]"></i>
          </button>
        </div>
      </div>
    </template>

    <template #toolbar>
      <!-- 执行中也保留：上传附件与粘贴截图同链路，粘贴在执行态一直可用 -->
      <div ref="plusMenuRef" class="relative">
        <button
          type="button"
          class="flex h-6 items-center gap-1.5 whitespace-nowrap rounded-full px-2 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
          title="更多操作"
          @click="plusMenuOpen = !plusMenuOpen"
        >
          <i class="fa-solid fa-plus text-[10px]"></i>
        </button>
        <div
          v-if="plusMenuOpen"
          class="absolute left-0 bottom-8 z-50 w-[160px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
        >
          <button
            type="button"
            class="flex w-full items-center gap-2 border-none bg-transparent px-3 py-2 text-left text-xs text-gray-600 cursor-pointer hover:bg-gray-50"
            @click="emit('uploadFile'); plusMenuOpen = false"
          >
            <i class="fa-solid fa-paperclip text-[11px] text-gray-400"></i>
            <span>上传文件</span>
          </button>
          <div class="border-t border-gray-100"></div>
          <button
            type="button"
            class="flex w-full items-center gap-2 border-none bg-transparent px-3 py-2 text-left text-xs text-gray-600 cursor-pointer hover:bg-gray-50"
            @click="emit('openSkillManager'); plusMenuOpen = false"
          >
            <i class="fa-solid fa-wand-magic-sparkles text-[11px] text-gray-400"></i>
            <span>技能管理</span>
          </button>
        </div>
      </div>
      <!-- 审批策略由管理员固定配置，不提供用户会话级切换入口 -->
      <div class="flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-1 text-xs text-slate-500">
        <i class="fa-solid fa-shield-halved text-[10px] text-blue-500"></i>
        <span>工具审批由管理员配置</span>
      </div>

      <slot name="toolbar-extra" />
    </template>

    <template #actions>
      <ModelPicker
        :selected-model="selectedModel"
        :model-options="modelOptions"
        :enable-deep-thinking="enableDeepThinking"
        :thinking-effort="thinkingEffort"
        :is-generating="isGenerating"
        @update:selected-model="emit('update:selectedModel', $event)"
        @update:enable-deep-thinking="emit('update:enableDeepThinking', $event)"
        @update:thinking-effort="emit('update:thinkingEffort', $event)"
      >
        <template #prefix>
          <slot name="model-prefix">
            <i class="fa-solid fa-microchip text-[10px] text-gray-400"></i>
          </slot>
        </template>
      </ModelPicker>
    </template>

    <template #footer>
      <slot name="command-menu" />
    </template>
  </ChatInputBox>
</template>

<style scoped>
.cap-switch {
  position: relative;
  display: inline-block;
  width: 26px;
  height: 14px;
  flex: 0 0 auto;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: #e5e7eb;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.cap-switch:hover {
  background: #d1d5db;
}

.cap-switch-on {
  background: #1677ff;
}

.cap-switch-on:hover {
  background: #0f6be8;
}

.cap-switch-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.22);
  transition: transform 0.15s ease;
}

.cap-switch-on .cap-switch-thumb {
  transform: translateX(12px);
}
</style>
