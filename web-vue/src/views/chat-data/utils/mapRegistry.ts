/**
 * 地图注册工具
 * 用于 ECharts 地图组件的地图数据注册
 */

import * as echarts from 'echarts'

let isMapRegistered = false
let isMapLoading = false
let mapLoadPromise: Promise<void> | null = null

/**
 * 注册中国地图（异步加载）
 * 确保只注册一次
 */
export async function registerChinaMap(): Promise<void> {
  if (isMapRegistered) return

  // 如果正在加载，返回同一个 promise
  if (isMapLoading && mapLoadPromise) {
    return mapLoadPromise
  }

  isMapLoading = true
  mapLoadPromise = (async () => {
    try {
      // 动态导入 GeoJSON
      const chinaGeoJson = await import('../assets/china.json')
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      echarts.registerMap('china', chinaGeoJson.default || chinaGeoJson as any)
      isMapRegistered = true
      console.log('[MapRegistry] 中国地图注册成功')
    } catch (error) {
      console.error('[MapRegistry] 中国地图注册失败:', error)
      throw error
    } finally {
      isMapLoading = false
    }
  })()

  return mapLoadPromise
}

/**
 * 检查地图是否已注册
 */
export function isChinaMapRegistered(): boolean {
  return isMapRegistered
}

/**
 * 省份名称标准化映射
 * 将常见的省份简称/别名映射到标准名称
 */
export const PROVINCE_NAME_MAP: Record<string, string> = {
  // 直辖市
  '北京': '北京市',
  '天津': '天津市',
  '上海': '上海市',
  '重庆': '重庆市',

  // 省份
  '河北': '河北省',
  '山西': '山西省',
  '辽宁': '辽宁省',
  '吉林': '吉林省',
  '黑龙江': '黑龙江省',
  '江苏': '江苏省',
  '浙江': '浙江省',
  '安徽': '安徽省',
  '福建': '福建省',
  '江西': '江西省',
  '山东': '山东省',
  '河南': '河南省',
  '湖北': '湖北省',
  '湖南': '湖南省',
  '广东': '广东省',
  '海南': '海南省',
  '四川': '四川省',
  '贵州': '贵州省',
  '云南': '云南省',
  '陕西': '陕西省',
  '甘肃': '甘肃省',
  '青海': '青海省',
  '台湾': '台湾省',

  // 自治区
  '内蒙古': '内蒙古自治区',
  '广西': '广西壮族自治区',
  '西藏': '西藏自治区',
  '宁夏': '宁夏回族自治区',
  '新疆': '新疆维吾尔自治区',

  // 特别行政区
  '香港': '香港特别行政区',
  '澳门': '澳门特别行政区',
}

/**
 * 标准化省份名称
 * @param name 原始省份名称
 * @returns 标准化后的省份名称
 */
export function normalizeProvinceName(name: string): string {
  if (!name) return name

  // 先去除空格
  const trimmed = name.trim()

  // 如果已经是标准名称，直接返回
  if (trimmed.endsWith('省') || trimmed.endsWith('市') ||
      trimmed.endsWith('自治区') || trimmed.endsWith('特别行政区')) {
    return trimmed
  }

  // 查找映射
  return PROVINCE_NAME_MAP[trimmed] || trimmed
}
