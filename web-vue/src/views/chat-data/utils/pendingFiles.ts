import type { UploadedFileInfo } from '../api'

/** 待发送附件 chip 的状态。 */
export interface PendingFile {
  file_id: string
  filename: string
  status: 'uploading' | 'uploaded' | 'failed'
  file_type?: string
  file_size?: number
  // 上传后实际存储文件名（去重后可能与 filename 不同），用于拼装可访问路径
  storage_dir?: string
}

/**
 * 上传成功后就地更新占位 chip，返回该 chip 是否仍待发送。
 *
 * 判定必须在改写 `file_id` **之前**完成：占位对象就是数组里的同一个引用，
 * 一旦先把临时 id 换成真实 id，再按临时 id 去数组里查就会恒为「已移除」，
 * 从而把刚上传成功的文件当成孤儿删掉。
 *
 * 返回 false 表示用户在上传期间移除了占位 chip，调用方需清理后端文件。
 */
export function settleUploadedPlaceholder(
  pendingFiles: PendingFile[],
  placeholder: PendingFile,
  uploaded: UploadedFileInfo,
): boolean {
  const stillPending = pendingFiles.includes(placeholder)
  placeholder.file_id = uploaded.file_id
  placeholder.filename = uploaded.original_filename
  placeholder.file_type = uploaded.file_type
  placeholder.file_size = uploaded.file_size
  placeholder.storage_dir = uploaded.storage_dir
  placeholder.status = 'uploaded'
  return stillPending
}
