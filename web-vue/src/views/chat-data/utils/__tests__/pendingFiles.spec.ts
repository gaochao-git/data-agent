import { describe, expect, it } from 'vitest'

import { settleUploadedPlaceholder, type PendingFile } from '../pendingFiles'

const uploaded = {
  file_id: 'real-1',
  original_filename: '用户输入.md',
  file_type: 'md',
  file_size: 128,
  storage_dir: '用户输入-2026-11-13.md',
}

const makePlaceholder = (): PendingFile => ({
  file_id: 'temp-1',
  filename: '用户输入-temp.md',
  status: 'uploading',
})

describe('settleUploadedPlaceholder', () => {
  it('keeps a still-pending placeholder and fills in the real file info', () => {
    const placeholder = makePlaceholder()
    const files = [placeholder]

    const stillPending = settleUploadedPlaceholder(files, placeholder, uploaded)

    expect(stillPending).toBe(true)
    expect(files[0]).toBe(placeholder)
    expect(files[0]).toEqual({
      file_id: 'real-1',
      filename: '用户输入.md',
      status: 'uploaded',
      file_type: 'md',
      file_size: 128,
      storage_dir: '用户输入-2026-11-13.md',
    })
    // 回归：占位对象就是数组里的同一引用，改写 file_id 后按临时 id 再查会恒为「已移除」，
    // 旧实现因此会把刚上传成功的文件误当成孤儿删掉。
    expect(files.some(file => file.file_id === 'temp-1')).toBe(false)
  })

  it('still reports pending after the array itself was replaced by filter', () => {
    const placeholder = makePlaceholder()
    const files = [placeholder].filter(file => file.file_id !== 'other-id')

    expect(settleUploadedPlaceholder(files, placeholder, uploaded)).toBe(true)
  })

  it('reports removal when the user dropped the placeholder while uploading', () => {
    const placeholder = makePlaceholder()

    const files = [placeholder].filter(file => file.file_id !== 'temp-1')

    expect(settleUploadedPlaceholder(files, placeholder, uploaded)).toBe(false)
  })
})
