<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

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

export interface ChatDataRuntimeOptions {
  workspace_backend: 'local' | 'sandbox'
  bash_enabled: boolean
}

interface Props {
  input: string
  placeholder: string
  /** 已选技能列表（title 展示、name 用于单项移除）。 */
  selectedSkills?: Array<{ name: string; title?: string }>
  /** 输入框 @ 引用的语义模型列表（多选，chip 展示、单项可移除；空 = 智能选择）。 */
  selectedSemanticModels?: Array<{ id: string; name: string }>
  selectedModel: string
  modelOptions: ModelOption[]
  /** 三态：true/false 用户显式选择；null 未触碰（展示回退模型 default_on）。 */
  enableDeepThinking: boolean | null
  /** 思考档位；null 未选择（展示回退模型 default_effort）。 */
  thinkingEffort: string | null
  isGenerating: boolean
  canSubmit: boolean
  inputFocused?: boolean
  showSkillButton?: boolean
  submitTitle?: string
  /** 系统默认运行选项（来自 /config 的 runtime_options）。 */
  runtimeOptions?: ChatDataRuntimeOptions
  /** 是否允许用户手动选择运行选项。关闭时面板只读。 */
  userOverridable?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  selectedSkills: () => [],
  selectedSemanticModels: () => [],
  inputFocused: false,
  showSkillButton: true,
  submitTitle: '发送 (Enter)',
  userOverridable: false,
})
const inputBoxRef = ref<InstanceType<typeof ChatInputBox> | null>(null)
const sandboxMenuRef = ref<HTMLElement | null>(null)
const sandboxMenuOpen = ref(false)
const plusMenuRef = ref<HTMLElement | null>(null)
const plusMenuOpen = ref(false)

const emit = defineEmits<{
  submit: []
  stop: []
  clearSkill: [name: string]
  clearSemanticModel: [modelId: string]
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
  'update:runtime-options': [value: ChatDataRuntimeOptions]
}>()

// ---- 能力面板：参考「模型选择 + 深度思考」的聚合下拉 ----
// 一个 pill 下拉内完成两件事：上面选隔离模式（本地/沙箱），下面放运行模式开关 Bash（参考深度思考）。
const sandboxModeOptions: { value: 'local' | 'sandbox'; label: string; desc: string; icon: string }[] = [
  { value: 'local', label: '本地', desc: '在本地目录进行操作', icon: 'fa-folder' },
  { value: 'sandbox', label: '沙箱', desc: '在隔离沙箱中操作', icon: 'fa-box' },
]

/** 初始隔离模式：本地则按默认，否则一律沙箱。 */
const workspaceBackend = ref<'local' | 'sandbox'>(
  props.runtimeOptions?.workspace_backend === 'sandbox' ? 'sandbox' : 'local'
)
/** Bash 是唯一影响工具注册的开关，与工作区后端独立。 */
const bashEnabled = ref<boolean>(
  !!props.runtimeOptions?.bash_enabled
)

const sandboxModeMeta = computed(
  () => sandboxModeOptions.find(o => o.value === workspaceBackend.value) ?? sandboxModeOptions[0]!
)
// pill 触发文字：模式 +（Bash 开启时附加 ·Bash 标识）
const sandboxTriggerLabel = computed(() =>
  bashEnabled.value ? `${sandboxModeMeta.value.label}·Bash` : sandboxModeMeta.value.label
)
const sandboxTriggerIcon = computed(() => sandboxModeMeta.value.icon)

const emitRuntimeOptions = () => {
  emit('update:runtime-options', {
    workspace_backend: workspaceBackend.value,
    bash_enabled: bashEnabled.value,
  })
}

const selectSandboxMode = (mode: 'local' | 'sandbox') => {
  workspaceBackend.value = mode
  emitRuntimeOptions()
}

const toggleBash = () => {
  bashEnabled.value = !bashEnabled.value
  emitRuntimeOptions()
}

watch(() => props.runtimeOptions, (value) => {
  if (!value) return
  workspaceBackend.value = value.workspace_backend === 'sandbox' ? 'sandbox' : 'local'
  bashEnabled.value = !!value.bash_enabled
}, { deep: true })

const handleDocumentClick = (event: MouseEvent) => {
  const target = event.target
  if (!(target instanceof Node)) return
  if (sandboxMenuRef.value?.contains(target)) return
  if (plusMenuRef.value?.contains(target)) return
  sandboxMenuOpen.value = false
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
        v-if="props.selectedSkills.length || props.selectedSemanticModels.length"
        class="mb-2 flex flex-wrap items-center gap-1.5"
      >
        <div
          v-for="model in props.selectedSemanticModels"
          :key="model.id"
          class="flex w-fit items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-xs text-blue-700"
          title="本轮提问使用该语义模型；移除后恢复智能选择"
        >
          <i class="fa-solid fa-cube text-[10px]"></i>
          <span class="max-w-[140px] truncate">@{{ model.name }}</span>
          <button type="button" class="text-blue-400 hover:text-blue-700" @click="emit('clearSemanticModel', model.id)">
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
      <!-- 能力面板：隔离模式 pill 下拉（上面选隔离模式，下面运行模式开关 Bash，参考模型选择+深度思考） -->
      <template v-if="userOverridable">
        <div ref="sandboxMenuRef" class="relative">
          <div class="flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-1 hover:bg-black/[0.05]">
            <button
              type="button"
              class="flex items-center gap-1 border-none bg-transparent p-0 text-xs cursor-pointer"
              title="切换隔离模式 / 运行模式"
              @click="sandboxMenuOpen = !sandboxMenuOpen"
            >
              <i class="fa-solid text-[10px]" :class="[sandboxTriggerIcon, workspaceBackend === 'local' ? 'text-green-500' : 'text-purple-500']"></i>
              <span :class="['truncate', workspaceBackend === 'local' ? 'text-green-600' : 'text-purple-600']">{{ sandboxTriggerLabel }}</span>
              <i class="fa-solid fa-chevron-down text-[8px] text-gray-400"></i>
            </button>
          </div>
          <!-- 下拉菜单：上面选隔离模式，下面运行模式开关 -->
          <div
            v-if="sandboxMenuOpen"
            class="absolute left-0 bottom-8 z-50 w-[230px] overflow-hidden rounded-lg border border-gray-200 bg-white py-1 text-xs text-gray-600 shadow-lg"
          >
            <!-- 隔离模式列表 -->
            <button
              v-for="opt in sandboxModeOptions"
              :key="opt.value"
              type="button"
              :class="[
                'flex w-full items-center justify-between gap-2 border-none bg-transparent px-3 py-2 text-left cursor-pointer',
                workspaceBackend === opt.value ? 'text-blue-600 bg-blue-50' : 'text-gray-600 hover:bg-gray-50'
              ]"
              @click="selectSandboxMode(opt.value)"
            >
              <div class="flex min-w-0 items-center gap-2">
                <i class="fa-solid text-[11px] text-gray-400" :class="opt.icon"></i>
                <div class="min-w-0">
                  <div class="font-medium">{{ opt.label }}</div>
                  <div class="text-[11px] text-gray-400">{{ opt.desc }}</div>
                </div>
              </div>
              <i v-if="workspaceBackend === opt.value" class="fa-solid fa-check text-[10px] flex-shrink-0"></i>
            </button>
            <!-- 分隔线 + Bash 开关（参考深度思考） -->
            <div class="my-1 border-t border-gray-100"></div>
            <button
              type="button"
              :class="[
                'flex w-full items-center justify-between gap-3 border-none bg-transparent px-3 py-2 text-left text-xs cursor-pointer',
                'hover:bg-gray-50'
              ]"
              role="switch"
              :aria-checked="bashEnabled"
              :title="bashEnabled ? '关闭 Bash 执行' : '开启 Bash 执行'"
              @click="toggleBash"
            >
              <span class="flex min-w-0 items-center gap-2">
                <i class="fa-solid fa-terminal text-[11px]" :class="bashEnabled ? 'text-orange-500' : 'text-gray-400'"></i>
                <span class="truncate">Bash</span>
              </span>
              <span :class="['cap-switch', bashEnabled && 'cap-switch-on']"><span class="cap-switch-thumb"></span></span>
            </button>
          </div>
        </div>
      </template>
      <!-- userOverridable=false 时完全隐藏隔离模式选择器 -->

      <div v-if="showSkillButton" ref="plusMenuRef" class="relative">
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
        <template #prefix="{ thinkingOn }">
          <slot name="model-prefix">
            <i
              :class="[
                'fa-solid fa-microchip text-[10px]',
                thinkingOn ? 'text-blue-500' : 'text-gray-400'
              ]"
            ></i>
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
