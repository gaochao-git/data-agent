<script setup lang="ts">
import type { SkillInfo } from '../../types'
import { getSkillCreatorLabel } from '../../utils'

interface Props {
  open: boolean
  loading?: boolean
  skills: SkillInfo[]
  highlightedIndex?: number
}

withDefaults(defineProps<Props>(), {
  loading: false,
  highlightedIndex: 0,
})

const emit = defineEmits<{
  select: [skill: SkillInfo]
}>()
</script>

<template>
  <div
    v-if="open"
    class="absolute left-0 right-0 bottom-full z-20 mb-2 max-h-[320px] overflow-y-auto rounded-xl border border-slate-200 bg-white text-left shadow-[0_12px_32px_rgba(15,23,42,0.16)]"
  >
    <div v-if="loading" class="px-3 py-3 text-xs text-slate-400">正在加载技能</div>
    <div v-else-if="skills.length === 0" class="px-3 py-3 text-xs text-slate-400">没有匹配的技能</div>
    <template v-else>
      <button
        v-for="(skill, index) in skills"
        :key="skill.skill_id || skill.name"
        type="button"
        :class="[
          'flex h-11 w-full items-center gap-2 border-none px-3 py-1.5 text-left transition-colors',
          index === highlightedIndex ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'
        ]"
        @mousedown.prevent="emit('select', skill)"
      >
        <i class="fa-solid fa-wand-magic-sparkles shrink-0 text-xs text-blue-500"></i>
        <span class="flex min-w-0 flex-1 items-center gap-2">
          <span class="max-w-[42%] shrink-0 truncate text-sm font-medium text-slate-800">{{ skill.title || skill.name }}</span>
          <span class="min-w-0 flex-1 truncate text-xs text-slate-400">{{ skill.description || skill.name }}</span>
        </span>
        <span
          :class="[
            'max-w-20 shrink-0 truncate rounded-md px-2 py-0.5 text-[11px] leading-5',
            skill.source === 'user' ? 'bg-blue-50 text-blue-600' : 'bg-slate-100 text-slate-500'
          ]"
        >
          <span :title="getSkillCreatorLabel(skill)">{{ getSkillCreatorLabel(skill) }}</span>
        </span>
      </button>
    </template>
  </div>
</template>
