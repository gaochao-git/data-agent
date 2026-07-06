export type SqlDialectType = 'mysql' | 'hive'
export type SqlFunctionGroupKey = 'common' | 'string' | 'date' | 'math' | 'condition'

export interface SqlFunctionCatalogItem {
  key: string
  label: string
  desc: string
  group: SqlFunctionGroupKey
  usesExpression?: boolean
  build: (expression: string) => string
}

export interface SqlFunctionOption {
  value: string
  label: string
  desc: string
  group: SqlFunctionGroupKey
  usesExpression?: boolean
}

const normalizeDialect = (dialect?: string | null): SqlDialectType => {
  return String(dialect || '').trim().toLowerCase() === 'hive' ? 'hive' : 'mysql'
}

const commonFunctions: SqlFunctionCatalogItem[] = [
  { key: 'RAW', label: '原始列', desc: '`col`', group: 'common', build: expr => expr },
]

const sharedStringFunctions: SqlFunctionCatalogItem[] = [
  { key: 'LOWER', label: 'LOWER', desc: '转小写', group: 'string', build: expr => `LOWER(${expr})` },
  { key: 'UPPER', label: 'UPPER', desc: '转大写', group: 'string', build: expr => `UPPER(${expr})` },
  { key: 'TRIM', label: 'TRIM', desc: '去首尾空格', group: 'string', build: expr => `TRIM(${expr})` },
  { key: 'LENGTH', label: 'LENGTH', desc: '字符串长度', group: 'string', build: expr => `LENGTH(${expr})` },
  { key: 'CHAR_LENGTH', label: 'CHAR_LENGTH', desc: '字符数', group: 'string', build: expr => `CHAR_LENGTH(${expr})` },
  { key: 'SUBSTRING', label: 'SUBSTRING', desc: '截取子串', group: 'string', build: expr => `SUBSTRING(${expr}, 1, 10)` },
  { key: 'SUBSTR', label: 'SUBSTR', desc: '截取子串', group: 'string', build: expr => `SUBSTR(${expr}, 1, 10)` },
  { key: 'REPLACE', label: 'REPLACE', desc: '替换字符', group: 'string', build: expr => `REPLACE(${expr}, '旧值', '新值')` },
  { key: 'CONCAT', label: 'CONCAT', desc: '拼接字符串', group: 'string', build: expr => `CONCAT(${expr}, '')` },
  { key: 'INSTR', label: 'INSTR', desc: '查找位置', group: 'string', build: expr => `INSTR(${expr}, '关键字')` },
  { key: 'LOCATE', label: 'LOCATE', desc: '查找位置', group: 'string', build: expr => `LOCATE('关键字', ${expr})` },
  { key: 'LPAD', label: 'LPAD', desc: '左侧补齐', group: 'string', build: expr => `LPAD(${expr}, 10, '0')` },
  { key: 'RPAD', label: 'RPAD', desc: '右侧补齐', group: 'string', build: expr => `RPAD(${expr}, 10, '0')` },
  { key: 'REPEAT', label: 'REPEAT', desc: '重复字符串', group: 'string', build: expr => `REPEAT(${expr}, 2)` },
  { key: 'REVERSE', label: 'REVERSE', desc: '反转字符串', group: 'string', build: expr => `REVERSE(${expr})` },
]

const mysqlStringFunctions: SqlFunctionCatalogItem[] = [
  ...sharedStringFunctions,
  { key: 'LEFT', label: 'LEFT', desc: '左截取', group: 'string', build: expr => `LEFT(${expr}, 10)` },
  { key: 'RIGHT', label: 'RIGHT', desc: '右截取', group: 'string', build: expr => `RIGHT(${expr}, 10)` },
]

const hiveStringFunctions: SqlFunctionCatalogItem[] = sharedStringFunctions

const mysqlDateFunctions: SqlFunctionCatalogItem[] = [
  { key: 'DATE', label: 'DATE', desc: '提取日期', group: 'date', build: expr => `DATE(${expr})` },
  { key: 'DATE_FORMAT_DAY', label: 'DATE_FORMAT(日)', desc: 'yyyy-MM-dd', group: 'date', build: expr => `DATE_FORMAT(${expr}, '%Y-%m-%d')` },
  { key: 'DATE_FORMAT_MONTH', label: 'DATE_FORMAT(月)', desc: 'yyyy-MM', group: 'date', build: expr => `DATE_FORMAT(${expr}, '%Y-%m')` },
  { key: 'DATE_FORMAT_YEAR', label: 'DATE_FORMAT(年)', desc: 'yyyy', group: 'date', build: expr => `DATE_FORMAT(${expr}, '%Y')` },
  { key: 'YEAR', label: 'YEAR', desc: '年份', group: 'date', build: expr => `YEAR(${expr})` },
  { key: 'MONTH', label: 'MONTH', desc: '月份', group: 'date', build: expr => `MONTH(${expr})` },
  { key: 'DAY', label: 'DAY', desc: '日', group: 'date', build: expr => `DAY(${expr})` },
  { key: 'HOUR', label: 'HOUR', desc: '小时', group: 'date', build: expr => `HOUR(${expr})` },
  { key: 'DATE_ADD_DAY', label: 'DATE_ADD', desc: '日期加天', group: 'date', build: expr => `DATE_ADD(${expr}, INTERVAL 1 DAY)` },
  { key: 'DATE_SUB_DAY', label: 'DATE_SUB', desc: '日期减天', group: 'date', build: expr => `DATE_SUB(${expr}, INTERVAL 1 DAY)` },
  { key: 'DATEDIFF', label: 'DATEDIFF', desc: '相差天数', group: 'date', build: expr => `DATEDIFF(CURRENT_DATE(), ${expr})` },
  { key: 'STR_TO_DATE_YYYYMMDD', label: 'STR_TO_DATE', desc: 'yyyyMMdd转日期', group: 'date', build: expr => `STR_TO_DATE(CAST(${expr} AS CHAR), '%Y%m%d')` },
  { key: 'CURRENT_DATE', label: 'CURRENT_DATE', desc: '当前日期', group: 'date', usesExpression: false, build: () => 'CURRENT_DATE()' },
  { key: 'CURRENT_TIMESTAMP', label: 'NOW', desc: '当前时间', group: 'date', usesExpression: false, build: () => 'NOW()' },
]

const hiveDateFunctions: SqlFunctionCatalogItem[] = [
  { key: 'TO_DATE', label: 'TO_DATE', desc: '提取日期', group: 'date', build: expr => `TO_DATE(${expr})` },
  { key: 'HIVE_DATE_FORMAT_DAY', label: 'DATE_FORMAT(日)', desc: 'yyyy-MM-dd', group: 'date', build: expr => `DATE_FORMAT(${expr}, 'yyyy-MM-dd')` },
  { key: 'HIVE_DATE_FORMAT_MONTH', label: 'DATE_FORMAT(月)', desc: 'yyyy-MM', group: 'date', build: expr => `DATE_FORMAT(${expr}, 'yyyy-MM')` },
  { key: 'HIVE_DATE_FORMAT_YEAR', label: 'DATE_FORMAT(年)', desc: 'yyyy', group: 'date', build: expr => `DATE_FORMAT(${expr}, 'yyyy')` },
  { key: 'HIVE_YYYYMMDD_DAY', label: 'yyyyMMdd转日', desc: 'yyyy-MM-dd', group: 'date', build: expr => `from_unixtime(unix_timestamp(${expr}, 'yyyyMMdd'), 'yyyy-MM-dd')` },
  { key: 'HIVE_YYYYMMDD_MONTH', label: 'yyyyMMdd转月', desc: 'yyyy-MM', group: 'date', build: expr => `from_unixtime(unix_timestamp(${expr}, 'yyyyMMdd'), 'yyyy-MM')` },
  { key: 'HIVE_YYYYMMDD_YEAR', label: 'yyyyMMdd转年', desc: 'yyyy', group: 'date', build: expr => `from_unixtime(unix_timestamp(${expr}, 'yyyyMMdd'), 'yyyy')` },
  { key: 'YEAR', label: 'YEAR', desc: '年份', group: 'date', build: expr => `YEAR(${expr})` },
  { key: 'MONTH', label: 'MONTH', desc: '月份', group: 'date', build: expr => `MONTH(${expr})` },
  { key: 'DAY', label: 'DAY', desc: '日', group: 'date', build: expr => `DAY(${expr})` },
  { key: 'HOUR', label: 'HOUR', desc: '小时', group: 'date', build: expr => `HOUR(${expr})` },
  { key: 'DATE_ADD_DAY', label: 'DATE_ADD', desc: '日期加天', group: 'date', build: expr => `DATE_ADD(${expr}, 1)` },
  { key: 'DATE_SUB_DAY', label: 'DATE_SUB', desc: '日期减天', group: 'date', build: expr => `DATE_SUB(${expr}, 1)` },
  { key: 'DATEDIFF', label: 'DATEDIFF', desc: '相差天数', group: 'date', build: expr => `DATEDIFF(CURRENT_DATE(), ${expr})` },
  { key: 'ADD_MONTHS', label: 'ADD_MONTHS', desc: '月份加减', group: 'date', build: expr => `ADD_MONTHS(${expr}, 1)` },
  { key: 'CURRENT_DATE', label: 'CURRENT_DATE', desc: '当前日期', group: 'date', usesExpression: false, build: () => 'CURRENT_DATE()' },
  { key: 'CURRENT_TIMESTAMP', label: 'CURRENT_TIMESTAMP', desc: '当前时间', group: 'date', usesExpression: false, build: () => 'CURRENT_TIMESTAMP()' },
]

const sharedMathFunctions: SqlFunctionCatalogItem[] = [
  { key: 'ABS', label: 'ABS', desc: '绝对值', group: 'math', build: expr => `ABS(${expr})` },
  { key: 'ROUND', label: 'ROUND', desc: '四舍五入', group: 'math', build: expr => `ROUND(${expr}, 2)` },
  { key: 'CEIL', label: 'CEIL', desc: '向上取整', group: 'math', build: expr => `CEIL(${expr})` },
  { key: 'FLOOR', label: 'FLOOR', desc: '向下取整', group: 'math', build: expr => `FLOOR(${expr})` },
  { key: 'POW', label: 'POW', desc: '幂运算', group: 'math', build: expr => `POW(${expr}, 2)` },
  { key: 'SQRT', label: 'SQRT', desc: '平方根', group: 'math', build: expr => `SQRT(${expr})` },
]

const mysqlMathFunctions: SqlFunctionCatalogItem[] = [
  ...sharedMathFunctions,
  { key: 'MOD', label: 'MOD', desc: '取余', group: 'math', build: expr => `MOD(${expr}, 10)` },
]

const hiveMathFunctions: SqlFunctionCatalogItem[] = [
  ...sharedMathFunctions,
  { key: 'MOD', label: '%', desc: '取余', group: 'math', build: expr => `(${expr} % 10)` },
]

const mysqlConditionFunctions: SqlFunctionCatalogItem[] = [
  {
    key: 'CASE_WHEN',
    label: 'CASE WHEN',
    desc: '条件分组',
    group: 'condition',
    build: expr => `CASE\n  WHEN ${expr} = '条件值1' THEN '结果1'\n  WHEN ${expr} = '条件值2' THEN '结果2'\n  ELSE '其他'\nEND`,
  },
  { key: 'IF', label: 'IF', desc: '条件判断', group: 'condition', build: expr => `IF(${expr} IS NULL, '空值', ${expr})` },
  { key: 'IFNULL', label: 'IFNULL', desc: '空值兜底', group: 'condition', build: expr => `IFNULL(${expr}, '默认值')` },
  { key: 'COALESCE', label: 'COALESCE', desc: '空值兜底', group: 'condition', build: expr => `COALESCE(${expr}, '默认值')` },
  { key: 'NULLIF', label: 'NULLIF', desc: '相等转空', group: 'condition', build: expr => `NULLIF(${expr}, '比较值')` },
]

const hiveConditionFunctions: SqlFunctionCatalogItem[] = [
  {
    key: 'CASE_WHEN',
    label: 'CASE WHEN',
    desc: '条件分组',
    group: 'condition',
    build: expr => `CASE\n  WHEN ${expr} = '条件值1' THEN '结果1'\n  WHEN ${expr} = '条件值2' THEN '结果2'\n  ELSE '其他'\nEND`,
  },
  { key: 'IF', label: 'IF', desc: '条件判断', group: 'condition', build: expr => `IF(${expr} IS NULL, '空值', ${expr})` },
  { key: 'NVL', label: 'NVL', desc: '空值兜底', group: 'condition', build: expr => `NVL(${expr}, '默认值')` },
  { key: 'COALESCE', label: 'COALESCE', desc: '空值兜底', group: 'condition', build: expr => `COALESCE(${expr}, '默认值')` },
  { key: 'NULLIF', label: 'NULLIF', desc: '相等转空', group: 'condition', build: expr => `NULLIF(${expr}, '比较值')` },
]

export const SQL_FUNCTION_GROUPS: Array<{ key: SqlFunctionGroupKey; label: string }> = [
  { key: 'common', label: '常用' },
  { key: 'string', label: '字符串函数' },
  { key: 'date', label: '日期函数' },
  { key: 'math', label: '数学函数' },
  { key: 'condition', label: '条件/空值' },
]

export const getSqlFunctionCatalog = (dialect?: string | null, includeRaw = false): SqlFunctionCatalogItem[] => {
  const normalizedDialect = normalizeDialect(dialect)
  return [
    ...(includeRaw ? commonFunctions : []),
    ...(normalizedDialect === 'hive' ? hiveStringFunctions : mysqlStringFunctions),
    ...(normalizedDialect === 'hive' ? hiveDateFunctions : mysqlDateFunctions),
    ...(normalizedDialect === 'hive' ? hiveMathFunctions : mysqlMathFunctions),
    ...(normalizedDialect === 'hive' ? hiveConditionFunctions : mysqlConditionFunctions),
  ]
}

export const getSqlFunctionOptions = (dialect?: string | null, includeRaw = false): SqlFunctionOption[] => {
  return getSqlFunctionCatalog(dialect, includeRaw).map(item => ({
    value: item.key,
    label: item.label,
    desc: item.desc,
    group: item.group,
    usesExpression: item.usesExpression,
  }))
}

export const buildSqlFunctionExpression = (functionKey: string, columnExpression: string, dialect?: string | null): string => {
  const normalizedExpression = (columnExpression || '').trim()
  const matched = getSqlFunctionCatalog(dialect, true).find(item => item.key === functionKey)
  return matched ? matched.build(normalizedExpression) : normalizedExpression
}

export const SQL_EXPRESSION_FUNCTION_KEYWORDS = [
  'abs',
  'add_months',
  'case',
  'cast',
  'ceil',
  'ceiling',
  'char',
  'char_length',
  'coalesce',
  'concat',
  'count',
  'current_date',
  'current_timestamp',
  'curdate',
  'curtime',
  'date',
  'date_add',
  'date_format',
  'date_sub',
  'datediff',
  'day',
  'dayofmonth',
  'dayofweek',
  'dayofyear',
  'else',
  'end',
  'floor',
  'from_unixtime',
  'hour',
  'if',
  'ifnull',
  'instr',
  'left',
  'length',
  'locate',
  'lower',
  'lpad',
  'ltrim',
  'max',
  'min',
  'minute',
  'mod',
  'month',
  'now',
  'nullif',
  'nvl',
  'pow',
  'power',
  'quarter',
  'rand',
  'repeat',
  'replace',
  'reverse',
  'right',
  'round',
  'rpad',
  'rtrim',
  'second',
  'sqrt',
  'str_to_date',
  'substr',
  'substring',
  'sum',
  'then',
  'to_date',
  'trim',
  'unix_timestamp',
  'upper',
  'week',
  'when',
  'year',
]
