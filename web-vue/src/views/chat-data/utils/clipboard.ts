import { ElMessage } from 'element-plus'

/**
 * 复制文本到剪贴板：clipboard API 需 secure context，内网 http 部署走 textarea 兜底。
 * 成功返回 true 并由调用方提示；失败弹统一错误提示（与 ModelAdminPanel 等既有范式一致）。
 */
export const copyTextToClipboard = async (text: string, successTip = '已复制'): Promise<boolean> => {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      ElMessage.success(successTip)
      return true
    } catch {
      // 权限拒绝或文档非激活，降级 execCommand
    }
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.append(textarea)
  textarea.select()
  try {
    if (!document.execCommand('copy')) throw new Error('execCommand copy returned false')
    ElMessage.success(successTip)
    return true
  } catch (error) {
    console.error('复制文本失败:', error)
    ElMessage.error('复制失败，请稍后重试')
    return false
  } finally {
    document.body.removeChild(textarea)
  }
}
