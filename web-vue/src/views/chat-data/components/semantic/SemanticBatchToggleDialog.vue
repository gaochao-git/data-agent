<script setup lang="ts">
/**
 * 批量启动 / 批量暂停语义模型
 * 一次请求完成整批切换，避免逐个点开关；无权限的候选模型不参与选择。
 * 目标状态由入口按钮决定（enabled），弹窗内不再切换方向。
 */
// props 走运行时声明 + PropType：ChatData 会嵌入未安装 typescript 的内网宿主项目，
// 直接 defineProps<{...}>() 引用外部类型会触发 @vue/compiler-sfc 加载 TS 而编译失败。
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'
import type { SemanticConfigItem } from '../../types'

const props = defineProps({
  configs: { type: Array as PropType<SemanticConfigItem[]>, default: () => [] },
  enabled: { type: Boolean, default: true },
})

const emit = defineEmits<{
  confirm: [configIds: string[], enabled: boolean]
}>()

const visible = defineModel<boolean>('visible', { default: false })
const selectedIds = ref<string[]>([])
const keyword = ref('')

const canManage = (config: SemanticConfigItem) => config.can_manage !== false
const manageableConfigs = computed(() => props.configs.filter(canManage))
const filteredConfigs = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return manageableConfigs.value
  return manageableConfigs.value.filter(config =>
    config.name.toLowerCase().includes(text) ||
    (config.description || '').toLowerCase().includes(text)
  )
})
const selectedIdSet = computed(() => new Set(selectedIds.value))
const selectedVisibleCount = computed(
  () => filteredConfigs.value.filter(config => selectedIdSet.value.has(config.id)).length
)
const allFilteredSelected = computed(
  () => filteredConfigs.value.length > 0 && selectedVisibleCount.value === filteredConfigs.value.length
)

const actionLabel = computed(() => (props.enabled ? '启动' : '暂停'))
const noTargetConfigs = computed(
  () => manageableConfigs.value.filter(config => config.enabled !== props.enabled).length === 0
)

// 默认选中「当前状态与目标相反」的模型，打开即是可直接执行的集合
const preselectIds = () => {
  selectedIds.value = manageableConfigs.value
    .filter(config => config.enabled !== props.enabled)
    .map(config => config.id)
}

const isSelected = (configId: string) => selectedIdSet.value.has(configId)
const toggleItem = (configId: string, checked: boolean) => {
  selectedIds.value = checked
    ? [...selectedIds.value, configId]
    : selectedIds.value.filter(id => id !== configId)
}
const toggleSelectAll = (checked: boolean) => {
  const filteredIds = filteredConfigs.value.map(config => config.id)
  selectedIds.value = checked
    ? [...new Set([...selectedIds.value, ...filteredIds])]
    : selectedIds.value.filter(id => !filteredIds.includes(id))
}

const confirm = () => {
  emit('confirm', [...selectedIds.value], props.enabled)
  visible.value = false
}

watch(visible, value => {
  if (!value) return
  keyword.value = ''
  preselectIds()
})
</script>

<template>
  <el-dialog v-model="visible" width="620px" :title="`批量${actionLabel}语义模型`">
    <div class="space-y-3">
      <div class="text-[12px] leading-5 text-gray-500">
        <template v-if="enabled">
          启动后模型会出现在会话页 @ 引用候选中，可被智能问数直接使用。
        </template>
        <template v-else>
          暂停后模型不会出现在会话页 @ 引用候选中，已保存的看板与历史回放不受影响。
        </template>
      </div>

      <div
        v-if="noTargetConfigs"
        class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-700"
      >
        当前没有处于「{{ enabled ? '已暂停' : '已启动' }}」状态的模型，可手动勾选其他模型。
      </div>

      <input
        v-model="keyword"
        type="text"
        placeholder="搜索模型名称"
        class="h-8 w-full px-3 border border-gray-200 rounded-md text-[13px] text-gray-700 outline-none focus:border-blue-500"
      />

      <div v-if="manageableConfigs.length === 0" class="py-8 text-center text-[13px] text-gray-400">
        没有可操作的语义模型
      </div>

      <template v-else>
        <label class="flex items-center gap-2 px-1 text-[13px] text-gray-600">
          <input
            type="checkbox"
            class="accent-blue-500"
            :checked="allFilteredSelected"
            :disabled="filteredConfigs.length === 0"
            @change="toggleSelectAll(($event.target as HTMLInputElement).checked)"
          />
          <span>全选（当前筛选 {{ filteredConfigs.length }} 个）</span>
        </label>

        <div class="max-h-[320px] overflow-y-auto border border-gray-200 rounded-md divide-y divide-gray-100">
          <label
            v-for="config in filteredConfigs"
            :key="config.id"
            class="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-gray-50"
          >
            <input
              type="checkbox"
              class="accent-blue-500 shrink-0"
              :checked="isSelected(config.id)"
              @change="toggleItem(config.id, ($event.target as HTMLInputElement).checked)"
            />
            <span class="min-w-0 flex-1 truncate text-[13px] text-gray-700">{{ config.name }}</span>
            <span
              class="shrink-0 rounded border px-1.5 py-px text-[11px] leading-4"
              :class="config.enabled
                ? 'border-emerald-200 bg-emerald-50 text-emerald-600'
                : 'border-slate-200 bg-slate-100 text-slate-500'"
            >{{ config.enabled ? '已启动' : '已暂停' }}</span>
          </label>
          <div v-if="filteredConfigs.length === 0" class="px-3 py-8 text-center text-[13px] text-gray-400">
            没有匹配的语义模型
          </div>
        </div>
      </template>
    </div>

    <template #footer>
      <div class="flex items-center gap-2">
        <span class="text-[11px] text-gray-400">已选 {{ selectedIds.length }} 个</span>
        <div class="ml-auto flex gap-2">
          <button
            class="px-3 py-1.5 border border-gray-200 rounded bg-white text-xs text-gray-600 hover:bg-gray-50"
            @click="visible = false"
          >
            取消
          </button>
          <button
            class="px-3 py-1.5 border-none rounded bg-[#1677FF] text-xs text-white hover:bg-[#0958D9] disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="selectedIds.length === 0"
            @click="confirm"
          >
            确认{{ actionLabel }}
          </button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>
