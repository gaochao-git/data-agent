<script setup lang="ts">
/** 写入类工具的「格式化 / 参数」视图切换，主对话与侧边栏共用。 */
const props = defineProps({
  modelValue: {
    type: String,
    default: 'formatted',
  },
})

const emit = defineEmits<{
  'update:modelValue': [value: 'formatted' | 'params']
}>()
</script>

<template>
  <div class="inline-flex shrink-0 rounded-lg border border-slate-200 bg-slate-50 p-0.5 text-[11px]">
    <button
      type="button"
      class="inline-flex items-center gap-1 rounded-md px-1.5 py-1 transition-colors"
      :class="props.modelValue === 'formatted' ? 'bg-white text-[#1f82d9] shadow-sm' : 'text-slate-500 hover:text-slate-700'"
      title="按文件类型格式化展示写入内容"
      data-file-write-view-formatted
      @click.stop="emit('update:modelValue', 'formatted')"
    >
      <i class="fa-solid fa-align-left text-[10px]"></i>
      <span>格式化</span>
    </button>
    <button
      type="button"
      class="inline-flex items-center gap-1 rounded-md px-1.5 py-1 transition-colors"
      :class="props.modelValue === 'params' ? 'bg-white text-[#1f82d9] shadow-sm' : 'text-slate-500 hover:text-slate-700'"
      title="查看工具原始参数"
      data-file-write-view-params
      @click.stop="emit('update:modelValue', 'params')"
    >
      <i class="fa-solid fa-code text-[10px]"></i>
      <span>参数</span>
    </button>
  </div>
</template>
