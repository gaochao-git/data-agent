<script setup lang="ts">
import type { SemanticModelPick } from '../../composables/useSemanticModelMention'

interface Props {
  open: boolean
  models: SemanticModelPick[]
  highlightedIndex?: number
}

withDefaults(defineProps<Props>(), {
  highlightedIndex: 0,
})

const emit = defineEmits<{
  select: [model: SemanticModelPick]
}>()
</script>

<template>
  <div
    v-if="open"
    class="absolute left-0 right-0 bottom-full z-20 mb-2 max-h-[320px] overflow-y-auto rounded-xl border border-slate-200 bg-white text-left shadow-[0_12px_32px_rgba(15,23,42,0.16)]"
  >
    <div v-if="models.length === 0" class="px-3 py-3 text-xs text-slate-400">没有匹配的语义模型</div>
    <template v-else>
      <button
        v-for="(model, index) in models"
        :key="model.id"
        type="button"
        :class="[
          'flex h-11 w-full items-center gap-2 border-none px-3 py-1.5 text-left transition-colors',
          index === highlightedIndex ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'
        ]"
        @mousedown.prevent="emit('select', model)"
      >
        <i class="fa-solid fa-cube shrink-0 text-xs text-blue-500"></i>
        <span class="flex min-w-0 flex-1 items-center gap-2">
          <span class="max-w-[42%] shrink-0 truncate text-sm font-medium text-slate-800">{{ model.name }}</span>
          <span class="min-w-0 flex-1 truncate text-xs text-slate-400">{{ model.description || '语义模型' }}</span>
        </span>
        <span
          v-if="model.metricsCount !== undefined || model.dimensionsCount !== undefined"
          class="max-w-24 shrink-0 truncate rounded-md bg-slate-100 px-2 py-0.5 text-[11px] leading-5 text-slate-500"
          :title="`指标 ${model.metricsCount ?? 0} 个 · 维度 ${model.dimensionsCount ?? 0} 个`"
        >
          指标 {{ model.metricsCount ?? 0 }} · 维度 {{ model.dimensionsCount ?? 0 }}
        </span>
      </button>
    </template>
  </div>
</template>
