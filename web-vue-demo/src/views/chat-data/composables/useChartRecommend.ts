/**
 * 图表推荐开关 - 共享状态
 * 模块级 ref，所有导入的组件共享同一个响应式引用
 */
import { ref } from 'vue'

const chartRecommendEnabled = ref(localStorage.getItem('chartRecommendEnabled') === 'true')

export function useChartRecommend() {
  const toggleChartRecommend = () => {
    chartRecommendEnabled.value = !chartRecommendEnabled.value
    localStorage.setItem('chartRecommendEnabled', String(chartRecommendEnabled.value))
  }

  return { chartRecommendEnabled, toggleChartRecommend }
}
