import { describe, expect, it } from 'vitest'
import {
  MAX_DOCUMENT_ANNOTATIONS,
  toDocumentAnnotationRequests,
  useDocumentAnnotations,
} from '../useDocumentAnnotations'

const base = {
  filePath: 'outputs/示例文档.docx',
  docPath: '/body/p[4]',
  quote: '这是一份示例文档',
  comment: '改成季度经营分析',
}

describe('useDocumentAnnotations', () => {
  it('adds, dedupes and removes annotations', () => {
    const { documentAnnotations, addDocumentAnnotation, removeDocumentAnnotation } = useDocumentAnnotations()
    expect(addDocumentAnnotation(base)).toEqual({ ok: true })
    expect(documentAnnotations.value).toHaveLength(1)
    expect(documentAnnotations.value[0]).toMatchObject(base)

    expect(addDocumentAnnotation(base)).toEqual({ ok: false, reason: 'duplicate' })
    expect(addDocumentAnnotation({ ...base, comment: '   ' })).toEqual({ ok: false, reason: 'empty' })

    removeDocumentAnnotation(documentAnnotations.value[0]!.id)
    expect(documentAnnotations.value).toHaveLength(0)
  })

  it('caps the annotation list', () => {
    const { documentAnnotations, addDocumentAnnotation } = useDocumentAnnotations()
    for (let index = 0; index < MAX_DOCUMENT_ANNOTATIONS; index += 1) {
      expect(addDocumentAnnotation({ ...base, comment: `要求 ${index}` })).toEqual({ ok: true })
    }
    expect(addDocumentAnnotation({ ...base, comment: '超出上限' })).toEqual({ ok: false, reason: 'limit' })
    expect(documentAnnotations.value).toHaveLength(MAX_DOCUMENT_ANNOTATIONS)
  })

  it('maps to snake_case request payload', () => {
    const { documentAnnotations, addDocumentAnnotation } = useDocumentAnnotations()
    addDocumentAnnotation(base)
    expect(toDocumentAnnotationRequests(documentAnnotations.value)).toEqual([{
      file_path: 'outputs/示例文档.docx',
      doc_path: '/body/p[4]',
      quote: '这是一份示例文档',
      comment: '改成季度经营分析',
    }])
  })

  it('aligns every chip with the shared conversation draft', () => {
    const {
      documentAnnotations,
      addDocumentAnnotation,
      alignDocumentAnnotationComment,
    } = useDocumentAnnotations()
    addDocumentAnnotation(base)
    addDocumentAnnotation({ ...base, docPath: '/body/p[8]', comment: '改成年度口径' })

    alignDocumentAnnotationComment('这段话的依据是什么？')

    expect(documentAnnotations.value.map(item => item.comment)).toEqual([
      '这段话的依据是什么？',
      '这段话的依据是什么？',
    ])
    alignDocumentAnnotationComment('   ')
    expect(documentAnnotations.value[0]?.comment).toBe('这段话的依据是什么？')
  })
})
