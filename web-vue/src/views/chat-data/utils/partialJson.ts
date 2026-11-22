/**
 * 从流式（可能未闭合）的工具参数 JSON 里提取某个字符串字段。
 *
 * 模型生成 tool_call.args 时前端拿到的是半截 JSON，不能直接 JSON.parse；
 * 这里只做「找到字段起始引号 → 逐字符解码到结束引号或文本末尾」，
 * 用于子智能体卡片在参数生成过程中展示任务书（description）。
 */
export const extractPartialJsonString = (raw: string, key: string): string => {
  const text = String(raw || '')
  if (!text || !key) return ''
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`(?:^|[{,])\\s*"${escapedKey}"\\s*:\\s*"`).exec(text)
  if (!match) return ''

  let index = match.index + match[0].length
  let result = ''
  while (index < text.length) {
    const char = text[index]
    if (char === '"') break
    if (char !== '\\') {
      result += char
      index += 1
      continue
    }
    const next = text[index + 1]
    if (next === undefined) break
    switch (next) {
      case '"': result += '"'; index += 2; break
      case '\\': result += '\\'; index += 2; break
      case '/': result += '/'; index += 2; break
      case 'b': result += '\b'; index += 2; break
      case 'f': result += '\f'; index += 2; break
      case 'n': result += '\n'; index += 2; break
      case 'r': result += '\r'; index += 2; break
      case 't': result += '\t'; index += 2; break
      case 'u': {
        const hex = text.slice(index + 2, index + 6)
        if (!/^[0-9a-fA-F]{4}$/.test(hex)) return result
        result += String.fromCharCode(parseInt(hex, 16))
        index += 6
        break
      }
      default: result += next; index += 2; break
    }
  }
  return result
}
