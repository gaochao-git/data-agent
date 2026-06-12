<template>
  <!-- 优先显示审核编辑器（人工审核） -->
  <ReviewEditor
    v-if="reviewEditor"
    :review-data="reviewEditor.reviewData"
    @confirm="(data, isEdited) => emit('review-confirm', data, isEdited)"
    @cancel="() => emit('review-cancel')"
    @view-law="handleViewLaw"
    @view-attachment="handleViewAttachment"
    :thread-id="threadId"
    :panel-height-class="panelHeightClass"
  />

  <!-- 其次显示相似案例报告 -->
  <CaseReportViewer
    v-else-if="caseReport"
    :key="caseReport.name"
    :report="caseReport"
    @close="onClose"
    :panel-height-class="panelHeightClass"
  />

  <!-- 否则显示文档预览器 -->
  <DocumentViewer
    v-else
    :document="document"
    :word-document="wordDocument"
    :excel-document="excelDocument"
    @close="onClose"
    @regenerate="onRegenerate"
    :panel-height-class="panelHeightClass"
    :thread-id="threadId"
  />

  <!-- 法律文档查看器弹窗 -->
  <LawContentPopover
    :is="lawViewerState !== null"
    :law-name="lawViewerState?.lawName || ''"
    :law-item-name="lawViewerState?.lawItemName || ''"
    :on-close="closeLawViewer"
  />
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import DocumentViewer from './DocumentViewer.vue'
import CaseReportViewer from './CaseReportViewer.vue'
import ReviewEditor from './ReviewEditor.vue'
import LawContentPopover from './LawContentPopover.vue'
import type { HumanReviewData } from '../types'

// 审核编辑数据类型
type ReviewEditData =
  | { violations: any[] }
  | { inspection_content: string; violation_facts: string }
  | { files: Array<{ file_index: number; inspection_content: string; violation_facts: string }> }
  | { evidence_list: any[]; basic_info: Record<string, string> }

// 报告源数据类型
interface ReportSourceData {
  report_type: string
  inspection_content?: string
  violation_facts?: string[]
  legal_basis?: string[]
  penalty_clauses?: string[]
}

interface Props {
  document: {
    law_name: string
    law_item_name: string
    law_item_content: string
  } | null
  wordDocument?: {
    filename: string
    title: string
    sourceData?: ReportSourceData
  } | null
  excelDocument?: {
    filename: string
    title: string
    excelData: any[] | { catalog: any[]; appendix: any[] }
  } | null
  caseReport?: {
    name: string
    content: string
  } | null
  reviewEditor?: {
    reviewData: HumanReviewData
    onConfirm: (data: ReviewEditData, isEdited: boolean) => void
    onCancel: () => void
  } | null
  onClose: () => void
  onRegenerate?: (filename: string, sourceData: ReportSourceData) => void
  panelHeightClass: string
  threadId: string
}

const props = defineProps<Props>()

// 法律文档查看器状态
const lawViewerState = ref<{ lawName: string; lawItemName: string } | null>(null)

// 事件发射
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'regenerate', filename: string, sourceData: ReportSourceData): void
  (e: 'view-law', lawName: string, lawItemName: string): void
  (e: 'view-attachment', file: any): void
  (e: 'review-confirm', data: ReviewEditData, isEdited: boolean): void
  (e: 'review-cancel'): void
}>()

// 处理查看法规
function handleViewLaw(lawName: string, lawItemName: string) {
  emit('view-law', lawName, lawItemName)
  lawViewerState.value = { lawName, lawItemName }
}

// 关闭法律文档查看器
function closeLawViewer() {
  lawViewerState.value = null
}

// 处理查看附件
function handleViewAttachment(file: any) {
  emit('view-attachment', file)
}
</script>
