/**
 * 极简确定性 YAML 序列化器，仅用于 Ossie 导入冲突的 diff 预览渲染。
 * 支持 string / number / boolean / null、普通对象与数组；键按写入顺序输出，不排序。
 * 不引入 js-yaml 依赖：这里只需「稳定、可对比」的文本，不需要完整 YAML 规范。
 */

/** 判定字符串可否作为 YAML 纯量裸写（不引号） */
const isPlainSafe = (text: string) =>
  text !== '' &&
  text.trim() === text &&
  !text.includes('\n') &&
  !/^-/.test(text) &&
  !/[:#{}[\],&*!|>'"%@`?]/.test(text) &&
  !['null', 'true', 'false', '~'].includes(text) &&
  !/^[0-9.+-]+$/.test(text)

const scalar = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '""'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  const text = String(value)
  // JSON.stringify 的双引号转义输出同时是合法的 YAML 字符串
  return isPlainSafe(text) ? text : JSON.stringify(text)
}

const write = (value: unknown, indent: number, lines: string[]) => {
  const pad = '  '.repeat(indent)
  if (Array.isArray(value)) {
    if (!value.length) {
      lines.push(`${pad}[]`)
      return
    }
    for (const item of value) {
      if (item && typeof item === 'object') {
        lines.push(`${pad}-`)
        write(item, indent + 1, lines)
      } else {
        lines.push(`${pad}- ${scalar(item)}`)
      }
    }
    return
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      // 空串与键缺失语义等价：跳过可避免「一侧空串、一侧缺失」的伪 diff
      .filter(([, v]) => v !== undefined && v !== '')
    if (!entries.length) {
      lines.push(`${pad}{}`)
      return
    }
    for (const [key, v] of entries) {
      if (v !== null && typeof v === 'object') {
        lines.push(`${pad}${key}:`)
        write(v, indent + 1, lines)
      } else {
        lines.push(`${pad}${key}: ${scalar(v)}`)
      }
    }
    return
  }
  lines.push(`${pad}${scalar(value)}`)
}

export const toYaml = (value: unknown): string => {
  const lines: string[] = []
  write(value, 0, lines)
  return lines.join('\n')
}
