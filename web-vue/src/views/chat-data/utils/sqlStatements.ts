/**
 * 多条 SQL 语句解析（自 SqlEditor.vue 抽出的纯函数）。
 * 按 `;` 切分，正确跳过 --/# 行注释、块注释与 "'\"` 三类引号
 * （含 \ 转义与 SQL 风格 '' 转义），空语句丢弃。
 */
export function parseSQLStatements(sqlText: string): string[] {
  const statements: string[] = []
  let current = ''
  let quoteChar = ''
  let inLineComment = false
  let inBlockComment = false

  for (let i = 0; i < sqlText.length; i++) {
    const char = sqlText[i]
    const nextChar = sqlText[i + 1]

    if (inLineComment) {
      current += char
      if (char === '\n') {
        inLineComment = false
      }
      continue
    }

    if (inBlockComment) {
      current += char
      if (char === '*' && nextChar === '/') {
        current += nextChar
        i++
        inBlockComment = false
      }
      continue
    }

    if (quoteChar) {
      current += char
      if (char === '\\') {
        if (i + 1 < sqlText.length) {
          current += sqlText[i + 1]
          i++
        }
        continue
      }
      if (char === quoteChar) {
        if (quoteChar === "'" && nextChar === "'") {
          current += nextChar
          i++
          continue
        }
        quoteChar = ''
      }
      continue
    }

    if (char === '-' && nextChar === '-') {
      inLineComment = true
      current += char + nextChar
      i++
      continue
    }
    if (char === '#') {
      inLineComment = true
      current += char
      continue
    }
    if (char === '/' && nextChar === '*') {
      inBlockComment = true
      current += char + nextChar
      i++
      continue
    }
    if (char === "'" || char === '"' || char === '`') {
      quoteChar = char
      current += char
      continue
    }

    if (char === ';') {
      const trimmed = current.trim()
      if (trimmed) {
        statements.push(trimmed)
      }
      current = ''
    } else {
      current += char
    }
  }

  const trimmed = current.trim()
  if (trimmed) {
    statements.push(trimmed)
  }

  return statements
}
