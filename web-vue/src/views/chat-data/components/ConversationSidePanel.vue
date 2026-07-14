<script setup lang="ts">
interface Props {
  open: boolean
  title: string
  subtitle?: string
  triggerLabel?: string
}

withDefaults(defineProps<Props>(), {
  subtitle: '',
  triggerLabel: '控制台',
})

const emit = defineEmits<{
  open: []
  close: []
}>()
</script>

<template>
  <button
    v-if="!open"
    type="button"
    class="absolute right-3 top-1/2 z-10 flex -translate-y-1/2 flex-col items-center gap-1 rounded-l-xl border border-r-0 border-slate-200 bg-white px-2.5 py-3 text-xs font-medium text-slate-500 shadow-[0_8px_24px_rgba(15,23,42,0.12)] transition-colors hover:bg-blue-50 hover:text-blue-700"
    @click="emit('open')"
  >
    <span class="[writing-mode:vertical-rl] tracking-wider">{{ triggerLabel }}</span>
  </button>

  <aside
    v-if="open"
    class="flex w-[40%] min-w-[380px] max-w-[580px] flex-col border-l border-slate-200 bg-white"
  >
    <div class="flex h-14 items-center justify-between border-b border-slate-100 px-4">
      <div class="min-w-0">
        <div class="text-sm font-semibold text-slate-800">{{ title }}</div>
        <div v-if="subtitle" class="mt-0.5 truncate font-mono text-xs text-slate-400">
          {{ subtitle }}
        </div>
      </div>
      <button
        type="button"
        class="h-8 w-8 rounded-md border border-slate-200 bg-white text-slate-500 hover:bg-slate-50"
        @click="emit('close')"
      >
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>
    <div class="flex-1 overflow-auto p-4">
      <slot />
    </div>
  </aside>
</template>
