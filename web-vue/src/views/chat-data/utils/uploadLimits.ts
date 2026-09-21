/**
 * 会话文件上传的统一限制（消息附件上传与工作区 uploads 上传共用同一口径）。
 */
export const ACCEPTED_FILE_TYPES = ['.txt', '.md', '.sql', '.docx', '.xlsx', '.pptx', '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif']
export const MAX_FILE_SIZE = 50 * 1024 * 1024
