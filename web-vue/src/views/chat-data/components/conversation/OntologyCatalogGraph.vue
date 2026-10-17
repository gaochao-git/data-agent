<script setup lang="ts">
/**
 * 本体目录图谱预览（workspace/ontology/_ontology_catalog.yaml 专用）。
 * 三要素全上图：节点=业务对象（颜色=治理状态）+ 属性（环绕对象的小节点，标识=蓝、标题=琥珀）；
 * 边=本体 relations 一比一（箭头 one端→many端，对齐 Palantir Vertex 默认方向）。
 * 力导向布局（对齐官方 Vertex 默认 Auto）：节点可拖拽重排、连线跟随、roam 缩放画布。
 * 数据全部来自文件文本前端解析，解析失败降级为提示文案不白屏；节点/边点击无行为。
 * 解析与 option 组装在 ontologyCatalogGraph.ts（纯函数层，单测覆盖）。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { buildGraphOption, parseOntologyCatalog } from './ontologyCatalogGraph'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const containerRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
const parseFailed = ref(false)

const renderChart = () => {
  if (!containerRef.value) return
  const catalog = parseOntologyCatalog(props.content)
  parseFailed.value = catalog === null
  if (!catalog) return
  if (!chart) chart = echarts.init(containerRef.value)
  chart.setOption(buildGraphOption(catalog), true)
}

// content 变化（刷新/重新物化）整图重建；setOption(notMerge=true) 保证旧节点不残留
watch(
  () => props.content,
  () => renderChart()
)

onMounted(() => {
  renderChart()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  if (containerRef.value) resizeObserver.observe(containerRef.value)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="ontology-catalog-graph min-w-0 overflow-hidden rounded-md border border-slate-100 bg-white">
    <div v-show="!parseFailed" ref="containerRef" class="h-full min-h-[280px] w-full"></div>
    <div
      v-if="parseFailed"
      class="flex h-full min-h-[280px] items-center justify-center px-6 text-center text-xs text-slate-400"
    >
      无法解析为本体目录（缺少 objects 定义），请切换源码视图查看
    </div>
  </div>
</template>
