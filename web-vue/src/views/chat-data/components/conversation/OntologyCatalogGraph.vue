<script setup lang="ts">
/**
 * 本体目录图谱预览（workspace/ontology/_ontology_catalog.yaml 专用）。
 * 三要素全上图：节点=业务对象（颜色=治理状态）+ 属性（环绕对象的小节点，标识=蓝、标题=琥珀）；
 * 边=本体 relations 一比一（箭头 one端→many端，对齐 Palantir Vertex 默认方向）。
 * 力导向布局（对齐官方 Vertex 默认 Auto）：节点可拖拽重排、连线跟随、roam 缩放画布。
 * 属性支持折叠（右上角开关，默认折叠）：对象/属性多时防止图谱杂乱，悬停对象 tooltip 仍可见属性清单。
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
// 属性折叠开关：默认折叠（防属性多图乱），展开后属性节点+挂接边全部上图
const showProperties = ref(false)

const renderChart = () => {
  if (!containerRef.value) return
  const catalog = parseOntologyCatalog(props.content)
  parseFailed.value = catalog === null
  if (!catalog) return
  if (!chart) chart = echarts.init(containerRef.value)
  chart.setOption(buildGraphOption(catalog, { showProperties: showProperties.value }), true)
}

// content 变化（刷新/重新物化）整图重建；setOption(notMerge=true) 保证旧节点不残留
watch(
  () => props.content,
  () => renderChart()
)

watch(showProperties, () => renderChart())

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
  <div class="relative min-w-0 overflow-hidden rounded-md border border-slate-100 bg-white">
    <div v-show="!parseFailed" ref="containerRef" class="h-full min-h-[280px] w-full"></div>
    <button
      v-if="!parseFailed"
      type="button"
      class="absolute left-2 top-2 z-10 rounded-full border border-slate-200 bg-white/90 px-2.5 py-0.5 text-[11px] text-slate-500 shadow-sm hover:border-slate-300 hover:text-slate-700"
      @click="showProperties = !showProperties"
    >
      {{ showProperties ? '收起属性' : '展开属性' }}
    </button>
    <div
      v-if="parseFailed"
      class="flex h-full min-h-[280px] items-center justify-center px-6 text-center text-xs text-slate-400"
    >
      无法解析为本体目录（缺少 objects 定义），请切换源码视图查看
    </div>
  </div>
</template>
