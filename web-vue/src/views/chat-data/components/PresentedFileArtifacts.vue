<script setup lang="ts">
import type { PropType } from 'vue'

type PresentedFileArtifactType = 'document' | 'spreadsheet' | 'presentation' | 'markdown' | 'html'

interface PresentedFileArtifact {
  path: string
  name: string
  artifactType: PresentedFileArtifactType
}

interface ArtifactDisplayMeta {
  label: string
  icon: string
  iconClass: string
}

const props = defineProps({
  artifacts: {
    type: Array as PropType<PresentedFileArtifact[]>,
    default: () => [],
  },
})

const emit = defineEmits<{
  preview: [artifact: PresentedFileArtifact]
  download: [artifact: PresentedFileArtifact]
}>()

const displayMeta: Record<PresentedFileArtifactType, ArtifactDisplayMeta> = {
  document: {
    label: 'Word 文档',
    icon: 'fa-regular fa-file-word',
    iconClass: 'bg-blue-600 text-white',
  },
  spreadsheet: {
    label: 'Excel 工作簿',
    icon: 'fa-regular fa-file-excel',
    iconClass: 'bg-emerald-600 text-white',
  },
  presentation: {
    label: 'PowerPoint 演示文稿',
    icon: 'fa-regular fa-file-powerpoint',
    iconClass: 'bg-orange-600 text-white',
  },
  markdown: {
    label: 'Markdown 文档',
    icon: 'fa-regular fa-file-lines',
    iconClass: 'bg-slate-700 text-white',
  },
  html: {
    label: '交互 HTML 页面',
    icon: 'fa-brands fa-html5',
    iconClass: 'bg-indigo-600 text-white',
  },
}

const getDisplayMeta = (artifactType: PresentedFileArtifactType): ArtifactDisplayMeta => displayMeta[artifactType]
</script>

<template>
  <div v-if="props.artifacts.length" class="mt-3 space-y-2">
    <div
      v-for="artifact in props.artifacts"
      :key="artifact.path"
      class="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/70 px-3.5 py-3"
    >
      <div class="flex min-w-0 items-center gap-2.5">
        <span
          class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
          :class="getDisplayMeta(artifact.artifactType).iconClass"
        >
          <i class="text-sm" :class="getDisplayMeta(artifact.artifactType).icon"></i>
        </span>
        <div class="min-w-0">
          <div class="truncate text-sm font-semibold text-slate-800">{{ artifact.name }}</div>
          <div class="mt-0.5 text-[11px] text-slate-500">{{ getDisplayMeta(artifact.artifactType).label }}</div>
        </div>
      </div>
      <div class="flex shrink-0 items-center gap-1">
        <button
          type="button"
          class="flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-100"
          :title="`预览 ${artifact.name}`"
          :aria-label="`预览 ${artifact.name}`"
          @click="emit('preview', artifact)"
        >
          <i class="fa-regular fa-eye text-xs"></i>
        </button>
        <button
          type="button"
          class="flex h-8 w-8 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 hover:bg-slate-100"
          :title="`下载 ${artifact.name}`"
          :aria-label="`下载 ${artifact.name}`"
          @click="emit('download', artifact)"
        >
          <i class="fa-solid fa-download text-xs"></i>
        </button>
      </div>
    </div>
  </div>
</template>
