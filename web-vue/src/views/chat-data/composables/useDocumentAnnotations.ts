import { ref } from 'vue'
import type { DocumentAnnotation, DocumentAnnotationRequest } from '../types'

/** 输入框最多保留的文档标注条数：与后端 parse_document_annotations 上限一致。 */
export const MAX_DOCUMENT_ANNOTATIONS = 5

export interface NewDocumentAnnotation {
  filePath: string
  docPath: string
  quote?: string
  comment: string
}

export type AddDocumentAnnotationResult =
  | { ok: true }
  | { ok: false; reason: 'empty' | 'limit' | 'duplicate' }

/** 文档标注 → 请求体：snake_case 对齐后端 options.annotations。 */
export const toDocumentAnnotationRequests = (
  annotations: DocumentAnnotation[],
): DocumentAnnotationRequest[] => annotations.map(item => ({
  file_path: item.filePath,
  doc_path: item.docPath,
  quote: item.quote,
  comment: item.comment,
}))

/**
 * 文档选中内容：Office 预览划选 → 「引用选中内容」→ 输入框 chip。
 *
 * 与划选引用（useQuotedSnippets）是两条独立通道：标注带文件与 OfficeCLI
 * 文档内定位，服务端据此拼出中性的 [文档选中内容] 标记块；划选可用于提问、
 * 核对或修改，是否动文件由用户问题决定。同文件同定位同要求的重复项直接忽略。
 */
export const useDocumentAnnotations = () => {
  const documentAnnotations = ref<DocumentAnnotation[]>([])
  let seq = 0

  const addDocumentAnnotation = (input: NewDocumentAnnotation): AddDocumentAnnotationResult => {
    const comment = String(input.comment || '').trim()
    if (!comment) return { ok: false, reason: 'empty' }
    if (documentAnnotations.value.length >= MAX_DOCUMENT_ANNOTATIONS) {
      return { ok: false, reason: 'limit' }
    }
    const duplicate = documentAnnotations.value.some(item => (
      item.filePath === input.filePath
      && item.docPath === input.docPath
      && item.comment === comment
    ))
    if (duplicate) return { ok: false, reason: 'duplicate' }
    seq += 1
    documentAnnotations.value = [
      ...documentAnnotations.value,
      {
        id: `doc-annotation-${seq}`,
        filePath: input.filePath,
        docPath: input.docPath,
        quote: String(input.quote || '').trim(),
        comment,
      },
    ]
    return { ok: true }
  }

  const removeDocumentAnnotation = (id: string) => {
    documentAnnotations.value = documentAnnotations.value.filter(item => item.id !== id)
  }

  const clearDocumentAnnotations = () => {
    documentAnnotations.value = []
  }

  /**
   * 浮层文本框与对话输入框共享同一份草稿：正文变化时让已有 chip 的
   * “问题或要求”跟随最终输入，避免 chip 和消息正文各显示一份旧内容。
   */
  const alignDocumentAnnotationComment = (value: string) => {
    const comment = String(value || '').trim()
    if (!comment || documentAnnotations.value.length === 0) return
    if (documentAnnotations.value.every(item => item.comment === comment)) return
    documentAnnotations.value = documentAnnotations.value.map(item => ({ ...item, comment }))
  }

  return {
    documentAnnotations,
    addDocumentAnnotation,
    removeDocumentAnnotation,
    clearDocumentAnnotations,
    alignDocumentAnnotationComment,
  }
}
