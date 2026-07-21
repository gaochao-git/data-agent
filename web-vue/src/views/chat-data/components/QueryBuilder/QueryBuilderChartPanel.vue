<script setup lang="ts">
import { computed } from 'vue'
import type { ChartType } from '../../chartTypes'
import type { AnalysisType } from './types'

type ChartFormat = { showIndex: boolean; autoWrap: boolean; metricsFirst: boolean }
type ChartPagination = { enabled: boolean; pageSize: number }

defineProps<{
  executing: boolean
  hasRawResult: boolean
  hasAnalyzedResult: boolean
  resultAnalysisFieldOptions: string[]
  resultAnalysisMetricOptions: string[]
  resultAnalysisDimensionOptions: string[]
  isCompareAnalysisDisabled: boolean
}>()

const emit = defineEmits<{
  runCompareAnalysis: []
  runSortAnalysis: []
  runTopNAnalysis: []
  runTotalAnalysis: []
  runPercentAnalysis: []
  runCompareBaselineAnalysis: []
  cancelResultAnalysis: []
}>()

const selectedChartType = defineModel<ChartType>('selectedChartType', { required: true })
const isChartPanelCollapsed = defineModel<boolean>('isChartPanelCollapsed', { required: true })
const chartPanelTab = defineModel<'chart' | 'analysis'>('chartPanelTab', { required: true })
const chartFormatCollapsed = defineModel<boolean>('chartFormatCollapsed', { required: true })
const chartFormat = defineModel<ChartFormat>('chartFormat', { required: true })
const chartPaginationCollapsed = defineModel<boolean>('chartPaginationCollapsed', {
  required: true,
})
const chartPagination = defineModel<ChartPagination>('chartPagination', { required: true })
const selectedAnalysisType = defineModel<AnalysisType>('selectedAnalysisType', { required: true })
const compareType = defineModel<'mom' | 'yoy'>('compareType', { required: true })
const resultSortField = defineModel<string>('resultSortField', { required: true })
const resultSortDirection = defineModel<'ASC' | 'DESC'>('resultSortDirection', { required: true })
const resultTopN = defineModel<number>('resultTopN', { required: true })
const topNField = defineModel<string>('topNField', { required: true })
const topNDirection = defineModel<'ASC' | 'DESC'>('topNDirection', { required: true })
const percentMetricField = defineModel<string>('percentMetricField', { required: true })
const percentScope = defineModel<'total' | 'group'>('percentScope', { required: true })
const percentGroupField = defineModel<string>('percentGroupField', { required: true })
const compareMetricField = defineModel<string>('compareMetricField', { required: true })
const compareBaselineMode = defineModel<'first' | 'max' | 'min' | 'avg'>('compareBaselineMode', {
  required: true,
})

const collapsedWidth = computed(() => (isChartPanelCollapsed.value ? '0px' : '220px'))
const runCompareAnalysis = () => emit('runCompareAnalysis')
const runSortAnalysis = () => emit('runSortAnalysis')
const runTopNAnalysis = () => emit('runTopNAnalysis')
const runTotalAnalysis = () => emit('runTotalAnalysis')
const runPercentAnalysis = () => emit('runPercentAnalysis')
const runCompareBaselineAnalysis = () => emit('runCompareBaselineAnalysis')
const cancelResultAnalysis = () => emit('cancelResultAnalysis')
const openChartPanel = () => {
  isChartPanelCollapsed.value = false
  chartPanelTab.value = 'chart'
}
const openAnalysisPanel = () => {
  isChartPanelCollapsed.value = false
  chartPanelTab.value = 'analysis'
}
</script>

<template>
  <!-- 图表类型面板 -->
  <div
    v-if="!isChartPanelCollapsed"
    class="flex flex-col border-r border-[#e3e5eb] bg-white shrink-0 transition-all duration-200"
    :style="{ width: collapsedWidth }"
  >
    <template v-if="!isChartPanelCollapsed">
      <!-- 顶部栏：Tab + 折叠按钮 -->
      <div class="flex items-center border-b border-[#e3e5eb] shrink-0 h-[40px]">
        <div class="flex-1 flex items-center justify-center">
          <button
            class="relative h-[40px] px-4 text-[13px] font-medium transition-colors"
            :class="
              chartPanelTab === 'chart' ? 'text-[#165DFF]' : 'text-[#57585a] hover:text-[#2e2f32]'
            "
            @click="chartPanelTab = 'chart'"
          >
            图表展示
            <span
              v-if="chartPanelTab === 'chart'"
              class="absolute bottom-0 left-1/2 -translate-x-1/2 w-full h-[2px] bg-[#165DFF] rounded-t"
            ></span>
          </button>
        </div>
        <div class="flex-1 flex items-center justify-center">
          <button
            class="relative h-[40px] px-4 text-[13px] font-medium transition-colors"
            :class="
              chartPanelTab === 'analysis'
                ? 'text-[#165DFF]'
                : 'text-[#57585a] hover:text-[#2e2f32]'
            "
            @click="chartPanelTab = 'analysis'"
          >
            结果分析
            <span
              v-if="chartPanelTab === 'analysis'"
              class="absolute bottom-0 left-1/2 -translate-x-1/2 w-full h-[2px] bg-[#165DFF] rounded-t"
            ></span>
          </button>
        </div>
        <button
          class="w-5 h-5 flex items-center justify-center rounded text-[#57585a] hover:bg-[#f0f1f3] transition-all mr-3 shrink-0"
          title="收起"
          @click="isChartPanelCollapsed = true"
        >
          <i class="fa-solid fa-chevron-left text-[10px]"></i>
        </button>
      </div>
      <!-- 可滚动内容 -->
      <div class="flex-1 overflow-y-auto">
        <!-- 图表 Tab -->
        <div v-show="chartPanelTab === 'chart'" class="px-4 py-2.5">
          <div class="grid grid-cols-5 gap-x-1 gap-y-3">
            <!-- 表格 TableSelected -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'table' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="表格"
              @click="selectedChartType = 'table'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <path fill="#fff" d="M1.596 6.872h16.863v10.455H1.596z" />
                <path
                  fill="#165DFF"
                  d="M18.125 2.083a.833.833 0 0 1 .834.834v14.166a.833.833 0 0 1-.834.834H1.875a.833.833 0 0 1-.833-.834V2.917a.833.833 0 0 1 .833-.834h16.25ZM1.875 7.292v9.791h16.25V7.292H1.875Z"
                />
                <path
                  fill="#8CB8FF"
                  d="M7.708 7.292v3.125h4.584V7.292h.833v3.125h5v3.541h-5v3.125h-.833v-3.125H7.708v3.125h-.833v-3.125h-5v-3.541h5V7.292h.833Z"
                />
              </svg>
            </button>
            <!-- 柱状图 PillarchartSelected -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'bar' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="柱状图"
              @click="selectedChartType = 'bar'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <path
                  fill="#8CB8FF"
                  d="M6.909 4.806a.836.836 0 0 1 .835.836v9.612a.836.836 0 0 1-.835.836H5.237a.836.836 0 0 1-.836-.836V5.642a.836.836 0 0 1 .836-.836H6.91Zm4.806-2.717a.836.836 0 0 1 .835.836v12.329a.836.836 0 0 1-.835.836h-1.672a.836.836 0 0 1-.836-.836V2.925a.836.836 0 0 1 .836-.835h1.672Zm4.805 6.06a.836.836 0 0 1 .836.836v6.269a.836.836 0 0 1-.836.836h-1.67a.836.836 0 0 1-.836-.836V8.985a.836.836 0 0 1 .836-.836h1.671Z"
                />
                <path
                  fill="#165DFF"
                  d="M2.292 1.667a.625.625 0 0 1 .625.625v14.791h14.792a.625.625 0 0 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.292a.625.625 0 0 1 .625-.625Zm14.23 6.482a.836.836 0 0 1 .835.836v2.89h-3.343v-2.89a.836.836 0 0 1 .836-.836h1.671ZM6.908 4.806a.836.836 0 0 1 .836.836v4.566H4.402V5.642a.836.836 0 0 1 .836-.836h1.671Zm4.807-2.716a.836.836 0 0 1 .835.836v5.616H9.208V2.925a.836.836 0 0 1 .836-.835h1.671Z"
                />
              </svg>
            </button>
            <!-- 条形图 BarchartSelected（横向） -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="
                selectedChartType === 'horizontal_bar' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'
              "
              title="条形图"
              @click="selectedChartType = 'horizontal_bar'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <path
                  fill="#8CB8FF"
                  d="M15.517 13.225a.833.833 0 0 0-.833-.833h-9.58a.833.833 0 0 0-.833.833v1.666a.833.833 0 0 0 .833.833h9.58a.834.834 0 0 0 .833-.833v-1.666Zm2.708-4.79a.833.833 0 0 0-.833-.833H5.105a.833.833 0 0 0-.833.833v1.666a.833.833 0 0 0 .833.833h12.287a.834.834 0 0 0 .833-.833V8.435Zm-6.04-4.79a.833.833 0 0 0-.833-.833H5.104a.833.833 0 0 0-.833.833v1.666a.833.833 0 0 0 .833.833h6.248a.833.833 0 0 0 .833-.833V3.645Z"
                />
                <path
                  fill="#165DFF"
                  d="M2.292 1.666a.625.625 0 0 1 .625.625v14.792h14.792a.625.625 0 1 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.29a.625.625 0 0 1 .625-.625Zm12.391 10.725a.833.833 0 0 1 .832.792l.001.041v1.666a.833.833 0 0 1-.791.832l-.042.002H8.435V12.39h6.248Zm2.707-4.79a.833.833 0 0 1 .832.792l.001.042V10.1a.833.833 0 0 1-.791.832l-.042.002h-5.623V7.6h5.623Zm-6.039-4.79a.833.833 0 0 1 .832.792l.001.042V5.31a.834.834 0 0 1-.792.831l-.041.002H9.268V2.81h2.083Z"
                />
              </svg>
            </button>
            <!-- 折线图 LinechartSelected -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'line' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="折线图"
              @click="selectedChartType = 'line'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <path
                  fill="#8CB8FF"
                  d="M11.976 9.682a.625.625 0 0 0 .733-.026l4.544-3.54a.625.625 0 0 0-.769-.986l-4.186 3.262-3.979-2.678a.625.625 0 0 0-.702.003L4.019 8.185a.625.625 0 0 0 .707 1.03l3.247-2.227 4.002 2.693Z"
                />
                <path
                  fill="#165DFF"
                  d="M2.292 1.666a.625.625 0 0 1 .625.625v14.792h14.792a.625.625 0 1 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.29a.625.625 0 0 1 .625-.625Zm14.193 8.046a.625.625 0 0 1 .768.985l-4.543 3.54a.625.625 0 0 1-.733.026L7.974 11.57l-3.247 2.228a.625.625 0 0 1-.707-1.03L7.617 10.3a.625.625 0 0 1 .703-.003l3.979 2.678 4.186-3.262Z"
                />
              </svg>
            </button>
            <!-- 面积图 -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'area' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="面积图"
              @click="selectedChartType = 'area'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <path
                  fill="#9BF"
                  fill-opacity=".5"
                  d="M4 8.2l3.6-2.5 4 2.7 4.5-3.5 1.9 1.5V17H4z"
                />
                <path
                  fill="#E5ECFF"
                  fill-opacity=".5"
                  d="M4 12.8l3.6-2.2 4 2.7 4.5-3.5 1.9 1.5V17H4z"
                />
                <path
                  fill="#165DFF"
                  d="M2.292 1.666a.625.625 0 0 1 .625.625v14.792h14.792a.625.625 0 1 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.29a.625.625 0 0 1 .625-.625Z"
                />
                <path
                  stroke="#165DFF"
                  stroke-width="1"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M4 8.2l3.6-2.5 4 2.7 4.5-3.5 1.9 1.5"
                />
                <path
                  stroke="#8CB8FF"
                  stroke-width="1"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M4 12.8l3.6-2.2 4 2.7 4.5-3.5 1.9 1.5"
                />
              </svg>
            </button>
            <!-- 饼图 PiechartSelected -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'pie' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="饼图"
              @click="selectedChartType = 'pie'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <g clip-path="url(#pie_clip)">
                  <path
                    fill="#165DFF"
                    d="m14.701 17.86-.123.072.123-.073Zm.118-.072-.074.045.074-.045Zm.13-.083a6.126 6.126 0 0 1-.08.052l.08-.052ZM10.002.833a9.163 9.163 0 0 1 4.989 16.845l.11-.073-5.104-7.603.005-.006V.833Z"
                  />
                  <path
                    fill="#8CB8FF"
                    d="m9.997 10.002 5.105 7.603A9.164 9.164 0 0 1 3.52 16.48l6.477-6.477Z"
                  />
                  <path fill="#9BF" d="M9.996.833v9.163H.833A9.163 9.163 0 0 1 9.996.833Z" />
                  <path
                    fill="#E5ECFF"
                    d="m9.996 9.996-6.48 6.48a9.164 9.164 0 0 1-2.683-6.48h9.163Z"
                  />
                </g>
                <defs>
                  <clipPath id="pie_clip"><path fill="#fff" d="M0 0h20v20H0z" /></clipPath>
                </defs>
              </svg>
            </button>
            <!-- 指标卡 measurecard-selected01 -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'kpi' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="指标卡"
              @click="selectedChartType = 'kpi'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <g clip-path="url(#card_clip)">
                  <path
                    fill="#165DFF"
                    d="M4.724 7.838c.064.58-.118 1.16-.487 1.553L2.3 12.4h2.424v1.047H.829v-1.006l2.424-3.608a1.55 1.55 0 0 0 .197-.954c0-.354 0-.803-.672-.803-.406 0-.673.204-.673.558v.6H.76V7.58c0-.899.87-1.607 2.017-1.607 1.46.013 1.948.858 1.948 1.865h-.001Zm8.551-1.865h1.287v7.474h-1.287V7.073l-1.287.802v-1.1l1.287-.802Zm5.95 1.865c.07.576-.103 1.155-.463 1.553L16.816 12.4h2.42v1.047h-3.915v-1.006l2.422-3.608a1.55 1.55 0 0 0 .196-.954c0-.354 0-.803-.672-.803-.405 0-.671.204-.671.558v.6h-1.332V7.58c0-.899.868-1.607 2.015-1.607 1.414.013 1.946.858 1.946 1.865Zm-8.739 1.738c.8.41.8.655.8 1.706s-.068 1.201-.324 1.501c-.424.477-1.003.716-1.588.655-.858 0-1.924-.396-1.924-1.556v-.627h1.32v.56c0 .354.267.545.661.545s.66-.246.66-.6v-1.065c0-.546-.196-.696-1.054-.696v-.86h-.14c.801 0 1.067-.095 1.067-.655V7.53c0-.3-.266-.505-.66-.505-.395 0-.708.3-.708.505v.6H7.31v-.6c0-.86.927-1.556 1.982-1.556a2.292 2.292 0 0 1 1.46.505c.395.355.534.655.534 1.556 0 .9-.069 1.2-.8 1.542v-.001Zm-3.41 1.859-.895 2.587h-.743l.652-2.587h.986Z"
                  />
                  <path
                    fill="#8CB8FF"
                    d="m2.379 16.393.015.022 1.37 2.253a.283.283 0 0 1-.016.303.13.13 0 0 1-.043.038c-.017.01-.055.015-.055.015H.91c-.083 0-.15-.096-.15-.215 0-.052.013-.102.036-.14l1.37-2.254c.054-.09.15-.1.213-.022Zm16.925.313v2.087h-14.1v-2.087h14.1Z"
                  />
                  <path fill="#9BF" d="M.76.403h8.403v3.07H.76V.403Z" />
                </g>
                <defs>
                  <clipPath id="card_clip"><path fill="#fff" d="M0 0h20v20H0z" /></clipPath>
                </defs>
              </svg>
            </button>
            <!-- 地图 MapSelected -->
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'map' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="地图"
              @click="selectedChartType = 'map'"
            >
              <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none">
                <path
                  fill="#D9E6FF"
                  d="m14.803 8.986.068.119s1.51.061 1.51.203c0 .143-1.047 1.221-.745 1.262.302.04.886 1.22.886 1.22s-.322-.04-.06.225c.262.264-.121.142-.322.345.284.136.443.123.443.123v.447s-.081.366-.323.631c-.241.265-.362.326-.362.326v.101s-.1.63-.322.936c-.222.305-.685.61-.886.753-.201.143-.926.061-.926.061s-.262.183-.322.326c-.045.106-.625.234-.919.293l-.053-.125.22-.445h.44v-.889l-.22-.444v-.222h-.66v-.89l-.44-.888h.44v-1.111h.22l.88.444h.44l.66.222.22-.444-.44-.222v-.445h.44l-.22-.444-.44-.222.22-.667.573-.58Zm1.477 6.06c.142-.55.504-.529.504-.529.262.204-.04.468 0 .723.04.254-.235.64-.235.64s-.027-.06-.268-.183c-.242-.122-.08-.427 0-.65v-.001Z"
                />
                <path
                  fill="#8CB8FF"
                  d="m16.208 5.565.44.222h.66l.44.223.66.222h.272a.642.642 0 0 1-.024.045c-.14.244-.14.244-.342.142-.02.366-.484.57-.484.57l.162.224h-.504s-.06.366-.261.447c-.202.082-.323.265-.403.428-.604.04-1.067 1.261-.826.285.092-.372.07-.538-.01-.579v-.007l-.004.005c-.133-.058-.413.202-.61.438l-.033.039c-.316.355-.692.45-.692.45l.154.267-.574.579-.22.667.44.222.22.444h-.44v.445l.44.222-.22.445-.66-.223h-.44l-.879-.444h-.22v1.111h-.44l.44.889v.889h.66v.222l.22.444v.89h-.44l-.22.444.053.125-.17.032s.101.184.101.346c0 .163.363.265.202.387-.161.122-.383.59-.765.529-.383-.061-.04-.672.2-.651.243.02.202-.204.041-.53-.16-.325.04-.264-.443-.142-.483.122-.765-.63-.825-.671-.06-.041-.322-.102-.463-.102-.141 0-.242.224-.322.224-.081 0-.303-.082-.303-.082s-.18.123-.281.123c-.101 0-.081-.184-.182-.184-.1 0-.18.204-.18.204l-.202-.082.06.57H8.77l-.121-.244-.181.102-.242-.366h-.231l.17-.408h-.17s.03-.203-.091-.406c-.906.06-.262-.407.09-.794.233-.255.075-.671-.077-.935l.155-.156h.22v-1.334l-.22-.444v-.667l.22-.222.44.667.439.222.44-.222.44-.445.44.445.22.444.44-.889.22-.222.439-.222-.22-.445v-.444l.66-.667 1.1-.666h.44l.22-.445.439-.222.44.222.44-.222.66-.222.439-.445-.22-.444h-.44v-.445l.44-.222Z"
                />
                <path
                  fill="#165DFF"
                  d="M14.085 4.04c-.003.013-.045.2.343.141.403-.061.403-.59.685-.916.282-.325-.222-.386-.222-.386.725-.814 1.188-.305 1.188-.305s.121-.041.322 0c.186.037.627 1.057.696 1.22.06.001.19.012.452.041.725.081.363.63.645.672.281.04.925-.652 1.046-.53.121.123-.16 1.303-.241 1.303-.08 0-.443.244-.343.346.09.09.132.344.04.572l-.068.034h-.22l-.66-.222-.44-.223h-.66l-.44-.222-.44.222v.445h.44l.22.444-.44.445-.659.222-.44.222-.44-.222-.44.222-.22.445h-.439l-1.1.666-.659.667v.444l.22.445-.44.222-.22.222-.44.889-.22-.444-.44-.445-.44.445-.44.222-.439-.222-.44-.667-.22.222v.667l.22.444v1.334h-.22l-.154.156a1.13 1.13 0 0 0-.174-.245c-.06-.04-.101.285-.162.265-.06-.02-.443-.184-.563-.184-.121 0-.463.306-.846.387-.382.081-.624-.102-.624-.102s.06-.203-.06-.37c-.121-.167-.383-.159-.625-.301-.241-.143-.302.488-.463.244-.16-.244-.1-.224-.382-.244-.282-.02-.403-.265-.725-.387-.322-.122-.383-.63-.564-.63-.181 0-.302-.59-.443-.652a.742.742 0 0 1-.021-.01c.017.02.016.059-.18.092-.363.06-.341-.429-.463-.509-.302-.081-.222-.305-.222-.447 0-.143.1-.367.1-.367s-.08.224.122.204c.201-.02-.02-.875.02-.835.04.041.181.102.221 0 .04-.101-.322-.223-.322-.223s.06-.224.06-.367c-.483-.203-.503-.63-.523-.712-.02-.081-.302-.305-.286-.447.013-.118.103-.042.134-.011l.011.01s.143-.095.08-.427c-.382-.203-.18-.508-.1-.691.08-.184.684-.102.684-.102s0 .448.222.244c.222-.204.342-.061.383-.142.04-.082 1.006-.204 1.006-.204V5.89s.262-.183.302-.366c.04-.183-.08-.61 0-.57.08.041.685-.02.645-.081-.04-.061.342-.814.423-.61.08.203.704.284.684.101-.02-.183.302-.427.362-.427s.363-.305.363-.305.14.081.1.224c-.04.142.162.345.202.407.04.06.282.183.282.284 0 .102.16.346.16.346l-.06.37-.1.383.886.265.423.407.281.549s.402.14.584.142c1.108 0 .866.285 1.088.285.221 0 .966.407.946.346-.02-.061.363-.224.544-.346.18-.122.584.081.865 0 .282-.081.469-.366.469-.366s.247-.09.135-.224c-.322-.183.02-.651.101-.55.08.102.443.164.443.164l.322-.367c.282.265.443-.203.463-.264.02-.061.415-.285.415-.285s.25.122.512 0c.261-.122-.222-.753-.705-.427-.02-.122-.343.122-.443 0-.1-.122.181-.936.181-.936Z"
                />
              </svg>
            </button>
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'funnel' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="漏斗图"
              @click="selectedChartType = 'funnel'"
            >
              <i class="fa-solid fa-filter text-[18px] text-[#165DFF]"></i>
            </button>
            <button
              class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer"
              :class="selectedChartType === 'graph' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'"
              title="关系图"
              @click="selectedChartType = 'graph'"
            >
              <i class="fa-solid fa-diagram-project text-[18px] text-[#165DFF]"></i>
            </button>
          </div>
          <!-- 基本格式 -->
          <div class="border-t border-[#e3e5eb]">
            <div
              class="flex items-center justify-between px-1 py-2.5 cursor-pointer select-none"
              @click="chartFormatCollapsed = !chartFormatCollapsed"
            >
              <span class="text-[12px] font-medium text-[#2e2f32]">基本格式</span>
              <i
                class="fa-solid fa-chevron-up text-[10px] text-[#57585a] transition-transform duration-150"
                :class="chartFormatCollapsed && 'rotate-180'"
              ></i>
            </div>
            <div v-show="!chartFormatCollapsed" class="px-1 pb-3">
              <div class="grid grid-cols-2 gap-x-2 gap-y-2">
                <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                  <input
                    type="checkbox"
                    v-model="chartFormat.showIndex"
                    class="accent-[#165DFF] w-3.5 h-3.5"
                  />序号
                </label>
                <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                  <input
                    type="checkbox"
                    v-model="chartFormat.autoWrap"
                    class="accent-[#165DFF] w-3.5 h-3.5"
                  />自动换行
                </label>
                <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                  <input
                    type="checkbox"
                    v-model="chartFormat.metricsFirst"
                    class="accent-[#165DFF] w-3.5 h-3.5"
                  />指标在前
                </label>
              </div>
            </div>
          </div>
          <!-- 翻页器 -->
          <div class="border-t border-[#e3e5eb]">
            <div class="flex items-center justify-between px-1 py-2.5">
              <div class="flex items-center gap-2">
                <span class="text-[12px] font-medium text-[#2e2f32]">翻页器</span>
                <button
                  class="relative w-[28px] h-[16px] rounded-full transition-colors duration-200"
                  :class="chartPagination.enabled ? 'bg-[#165DFF]' : 'bg-[#c9cdd4]'"
                  @click="chartPagination.enabled = !chartPagination.enabled"
                >
                  <span
                    class="absolute top-[2px] w-[12px] h-[12px] bg-white rounded-full shadow transition-all duration-200"
                    :class="chartPagination.enabled ? 'left-[14px]' : 'left-[2px]'"
                  ></span>
                </button>
              </div>
              <i
                class="fa-solid fa-chevron-up text-[10px] text-[#57585a] cursor-pointer transition-transform duration-150"
                :class="chartPaginationCollapsed && 'rotate-180'"
                @click="chartPaginationCollapsed = !chartPaginationCollapsed"
              ></i>
            </div>
            <div v-show="!chartPaginationCollapsed" class="px-1 pb-3">
              <div class="flex items-center gap-2 mb-2">
                <span class="text-[12px] text-[#57585a] shrink-0">每页显示</span>
                <select
                  v-model.number="chartPagination.pageSize"
                  class="w-[52px] px-1 py-0.5 border border-[#e3e5eb] rounded text-[12px] bg-white text-[#2e2f32] outline-none cursor-pointer"
                >
                  <option :value="10">10</option>
                  <option :value="20">20</option>
                  <option :value="50">50</option>
                  <option :value="100">100</option>
                </select>
                <span class="text-[12px] text-[#57585a]">条/页</span>
              </div>
            </div>
          </div>
        </div>
        <!-- 分析 Tab -->
        <div v-show="chartPanelTab === 'analysis'" class="px-3 pt-3 pb-4">
          <!-- 分析类型图标网格 -->
          <div class="grid grid-cols-3 gap-x-1 gap-y-3 mb-3">
            <!-- 排序 SortSelected -->
            <button
              class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all"
              :class="
                selectedAnalysisType === 'sort'
                  ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]'
                  : 'hover:bg-[#f0f1f3]'
              "
              @click="selectedAnalysisType = 'sort'"
            >
              <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 21 20" fill="none">
                <path
                  fill="#165DFF"
                  fill-rule="evenodd"
                  d="M10.042 2.083h7.917c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-7.917a.833.833 0 0 1-.833-.834V2.917c0-.46.373-.834.833-.834Zm0 4.375h6.459c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-6.459a.833.833 0 0 1-.833-.834V7.292c0-.46.373-.834.833-.834Zm0 4.375h4.584c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-4.584a.833.833 0 0 1-.833-.834v-1.041c0-.46.373-.834.833-.834Zm0 4.375h2.709c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-2.709a.833.833 0 0 1-.833-.834v-1.041c0-.46.373-.834.833-.834Z"
                  clip-rule="evenodd"
                />
                <path
                  fill="#8CB8FF"
                  fill-rule="evenodd"
                  d="M4.981 1.875c.46 0 .834.373.834.833v10.834c0 .072-.01.142-.026.208h1.84a.417.417 0 0 1 .34.658l-.021.027-3.064 3.31a.417.417 0 0 1-.615.025l-.023-.025-3.064-3.31a.417.417 0 0 1 .288-.684l.03-.001h1.841a.835.835 0 0 1-.026-.208V2.708c0-.46.373-.833.833-.833h.833Z"
                  clip-rule="evenodd"
                />
              </svg>
              <span
                class="text-[10px] leading-tight"
                :class="selectedAnalysisType === 'sort' ? 'text-[#165DFF]' : 'text-[#57585a]'"
                >排序</span
              >
            </button>
            <!-- Top N（显示数量） -->
            <button
              class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all"
              :class="
                selectedAnalysisType === 'topn'
                  ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]'
                  : 'hover:bg-[#f0f1f3]'
              "
              @click="selectedAnalysisType = 'topn'"
            >
              <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 21 20" fill="none">
                <path
                  fill="#165DFF"
                  fill-rule="evenodd"
                  d="M2.542 10h2.709c.46 0 .833.373.833.833v5.834c0 .46-.373.833-.833.833H2.542a.833.833 0 0 1-.833-.833v-5.834c0-.46.373-.833.833-.833Z"
                  clip-rule="evenodd"
                />
                <path
                  fill="#8CB8FF"
                  fill-rule="evenodd"
                  d="M8.583 2.5H11.5c.46 0 .833.373.833.833v13.334c0 .46-.373.833-.833.833H8.583a.833.833 0 0 1-.833-.833V3.333c0-.46.373-.833.833-.833Z"
                  clip-rule="evenodd"
                />
                <path
                  fill="#165DFF"
                  fill-rule="evenodd"
                  d="M14.833 5.833h2.709c.46 0 .833.373.833.834v10c0 .46-.373.833-.833.833h-2.709a.833.833 0 0 1-.833-.833v-10c0-.46.373-.834.833-.834Z"
                  clip-rule="evenodd"
                />
              </svg>
              <span
                class="text-[10px] leading-tight"
                :class="selectedAnalysisType === 'topn' ? 'text-[#165DFF]' : 'text-[#57585a]'"
                >Top N</span
              >
            </button>
            <!-- 合计 -->
            <button
              class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all"
              :class="
                selectedAnalysisType === 'total'
                  ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]'
                  : 'hover:bg-[#f0f1f3]'
              "
              @click="selectedAnalysisType = 'total'"
            >
              <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 24 24" fill="none">
                <path
                  d="M17 5H7l5 7-5 7h10"
                  stroke="#165DFF"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <span
                class="text-[10px] leading-tight"
                :class="selectedAnalysisType === 'total' ? 'text-[#165DFF]' : 'text-[#57585a]'"
                >合计</span
              >
            </button>
            <!-- 百分比 -->
            <button
              class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all"
              :class="
                selectedAnalysisType === 'percent'
                  ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]'
                  : 'hover:bg-[#f0f1f3]'
              "
              @click="selectedAnalysisType = 'percent'"
            >
              <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 24 24" fill="none">
                <circle cx="8" cy="8" r="2.5" stroke="#165DFF" stroke-width="1.8" />
                <circle cx="16" cy="16" r="2.5" stroke="#165DFF" stroke-width="1.8" />
                <path d="M18 6L6 18" stroke="#8CB8FF" stroke-width="1.8" stroke-linecap="round" />
              </svg>
              <span
                class="text-[10px] leading-tight"
                :class="selectedAnalysisType === 'percent' ? 'text-[#165DFF]' : 'text-[#57585a]'"
                >百分比</span
              >
            </button>
            <!-- 对比 -->
            <button
              class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all"
              :class="
                selectedAnalysisType === 'compare'
                  ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]'
                  : 'hover:bg-[#f0f1f3]'
              "
              @click="selectedAnalysisType = 'compare'"
            >
              <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 24 24" fill="none">
                <rect x="3" y="5" width="10" height="10" rx="2" fill="#165DFF" fill-opacity="0.6" />
                <rect
                  x="11"
                  y="9"
                  width="10"
                  height="10"
                  rx="2"
                  fill="#8CB8FF"
                  fill-opacity="0.6"
                />
              </svg>
              <span
                class="text-[10px] leading-tight"
                :class="selectedAnalysisType === 'compare' ? 'text-[#165DFF]' : 'text-[#57585a]'"
                >对比</span
              >
            </button>
            <!-- 同环比 -->
            <button
              class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all"
              :class="
                selectedAnalysisType === 'yoy'
                  ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]'
                  : 'hover:bg-[#f0f1f3]'
              "
              @click="selectedAnalysisType = 'yoy'"
            >
              <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 21 20" fill="none">
                <circle cx="10.042" cy="10" r="2.5" fill="#165DFF" />
                <path
                  fill="#165DFF"
                  fill-rule="evenodd"
                  d="M8.72 4.87a.833.833 0 0 0-1.1-.423A6.083 6.083 0 0 0 4 10.003a6.082 6.082 0 0 0 3.602 5.549.833.833 0 1 0 .682-1.52 4.416 4.416 0 0 1-2.617-4.029 4.416 4.416 0 0 1 2.63-4.033c.42-.187.61-.68.423-1.1Zm3.764-.42a.833.833 0 0 0-.689 1.517 4.431 4.431 0 0 1 2.622 4.03c0 1.751-1.042 3.318-2.634 4.035a.833.833 0 1 0 .684 1.52c2.183-.983 3.616-3.14 3.616-5.555 0-2.41-1.425-4.56-3.599-5.548Z"
                  clip-rule="evenodd"
                />
                <path
                  fill="#8CB8FF"
                  fill-rule="evenodd"
                  d="M13.024 1.295a.833.833 0 0 0-.542 1.576A7.53 7.53 0 0 1 17.542 10a7.53 7.53 0 0 1-5.063 7.13.833.833 0 0 0 .542 1.576A9.197 9.197 0 0 0 19.208 10a9.197 9.197 0 0 0-6.184-8.704Zm-4.901.52a.833.833 0 0 0-1.057-.52C3.396 2.538.875 6.026.875 10.001c0 3.97 2.514 7.455 6.179 8.703a.833.833 0 0 0 .537-1.577c-2.991-1.02-5.05-3.872-5.05-7.126 0-3.258 2.064-6.113 5.06-7.13a.833.833 0 0 0 .522-1.056Z"
                  clip-rule="evenodd"
                />
              </svg>
              <span
                class="text-[10px] leading-tight"
                :class="selectedAnalysisType === 'yoy' ? 'text-[#165DFF]' : 'text-[#57585a]'"
                >同环比</span
              >
            </button>
          </div>
          <!-- 分隔线 -->
          <div class="border-t border-[#e3e5eb] mb-3"></div>
          <!-- 排序配置 -->
          <template v-if="selectedAnalysisType === 'sort'">
            <div class="space-y-3">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[12px] font-medium text-[#2e2f32]">排序</span>
              </div>
              <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                <div class="text-[11px] text-[#57585a] mb-1.5">基于当前查询结果排序，不改 SQL</div>
                <select
                  v-model="resultSortField"
                  class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]"
                >
                  <option v-for="field in resultAnalysisFieldOptions" :key="field" :value="field">
                    {{ field }}
                  </option>
                </select>
                <div class="flex items-center gap-4">
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      v-model="resultSortDirection"
                      type="radio"
                      value="ASC"
                      class="accent-[#165DFF]"
                    />
                    升序
                  </label>
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      v-model="resultSortDirection"
                      type="radio"
                      value="DESC"
                      class="accent-[#165DFF]"
                    />
                    降序
                  </label>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasRawResult || executing || !resultSortField"
                  @click="runSortAnalysis"
                >
                  开始分析
                </button>
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasAnalyzedResult"
                  @click="cancelResultAnalysis"
                >
                  取消分析
                </button>
              </div>
            </div>
          </template>
          <!-- Top N 配置（显示数量） -->
          <template v-else-if="selectedAnalysisType === 'topn'">
            <div class="space-y-3">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[12px] font-medium text-[#2e2f32]">Top N</span>
              </div>
              <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                <div class="text-[11px] text-[#57585a] mb-1.5">排序字段</div>
                <select
                  v-model="topNField"
                  class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]"
                >
                  <option v-for="field in resultAnalysisFieldOptions" :key="field" :value="field">
                    {{ field }}
                  </option>
                </select>
                <div class="flex items-center gap-4 mb-2">
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      v-model="topNDirection"
                      type="radio"
                      value="DESC"
                      class="accent-[#165DFF]"
                    />
                    取最大前 N
                  </label>
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      v-model="topNDirection"
                      type="radio"
                      value="ASC"
                      class="accent-[#165DFF]"
                    />
                    取最小前 N
                  </label>
                </div>
                <div class="text-[11px] text-[#57585a] mb-1.5">显示数量</div>
                <input
                  v-model.number="resultTopN"
                  type="number"
                  min="1"
                  max="10000"
                  class="w-full px-2 py-1.5 border border-[#e3e5eb] rounded text-[12px] text-[#2e2f32] outline-none focus:border-[#165DFF] bg-white"
                />
              </div>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasRawResult || executing || !topNField"
                  @click="runTopNAnalysis"
                >
                  开始分析
                </button>
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasAnalyzedResult"
                  @click="cancelResultAnalysis"
                >
                  取消分析
                </button>
              </div>
            </div>
          </template>
          <template v-else-if="selectedAnalysisType === 'total'">
            <div class="space-y-3">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[12px] font-medium text-[#2e2f32]">合计</span>
              </div>
              <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                <div class="text-[11px] text-[#57585a] leading-5">
                  基于当前显示结果追加一行“合计”，对数值列求和，不改 SQL
                </div>
              </div>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasRawResult || executing"
                  @click="runTotalAnalysis"
                >
                  开始分析
                </button>
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasAnalyzedResult"
                  @click="cancelResultAnalysis"
                >
                  取消分析
                </button>
              </div>
            </div>
          </template>
          <template v-else-if="selectedAnalysisType === 'percent'">
            <div class="space-y-3">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[12px] font-medium text-[#2e2f32]">百分比</span>
              </div>
              <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                <div class="text-[11px] text-[#57585a] mb-1.5">指标</div>
                <select
                  v-model="percentMetricField"
                  class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]"
                >
                  <option v-for="field in resultAnalysisMetricOptions" :key="field" :value="field">
                    {{ field }}
                  </option>
                </select>
                <div class="text-[11px] text-[#57585a] mb-1.5">计算范围</div>
                <div class="flex items-center gap-4 mb-2">
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      v-model="percentScope"
                      type="radio"
                      value="total"
                      class="accent-[#165DFF]"
                    />
                    整体占比
                  </label>
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      v-model="percentScope"
                      type="radio"
                      value="group"
                      class="accent-[#165DFF]"
                    />
                    组内占比
                  </label>
                </div>
                <template v-if="percentScope === 'group'">
                  <div class="text-[11px] text-[#57585a] mb-1.5">分组维度</div>
                  <select
                    v-model="percentGroupField"
                    class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none text-[#2e2f32]"
                  >
                    <option
                      v-for="field in resultAnalysisDimensionOptions"
                      :key="field"
                      :value="field"
                    >
                      {{ field }}
                    </option>
                  </select>
                </template>
              </div>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="
                    !hasRawResult ||
                    executing ||
                    !percentMetricField ||
                    (percentScope === 'group' && !percentGroupField)
                  "
                  @click="runPercentAnalysis"
                >
                  开始分析
                </button>
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasAnalyzedResult"
                  @click="cancelResultAnalysis"
                >
                  取消分析
                </button>
              </div>
            </div>
          </template>
          <template v-else-if="selectedAnalysisType === 'compare'">
            <div class="space-y-3">
              <div class="flex items-center justify-between mb-2">
                <span class="text-[12px] font-medium text-[#2e2f32]">对比</span>
              </div>
              <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                <div class="text-[11px] text-[#57585a] mb-1.5">指标</div>
                <select
                  v-model="compareMetricField"
                  class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]"
                >
                  <option v-for="field in resultAnalysisMetricOptions" :key="field" :value="field">
                    {{ field }}
                  </option>
                </select>
                <div class="text-[11px] text-[#57585a] mb-1.5">对比基准</div>
                <select
                  v-model="compareBaselineMode"
                  class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none text-[#2e2f32]"
                >
                  <option value="first">首行</option>
                  <option value="max">最大值</option>
                  <option value="min">最小值</option>
                  <option value="avg">平均值</option>
                </select>
              </div>
              <div class="text-[11px] leading-5 text-[#939495]">
                基于当前显示结果追加基准值、差值、差异率列，不改 SQL
              </div>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasRawResult || executing || !compareMetricField"
                  @click="runCompareBaselineAnalysis"
                >
                  开始分析
                </button>
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasAnalyzedResult"
                  @click="cancelResultAnalysis"
                >
                  取消分析
                </button>
              </div>
            </div>
          </template>
          <template v-else-if="selectedAnalysisType === 'yoy'">
            <div class="space-y-3">
              <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                <div class="text-[11px] text-[#57585a] mb-1.5">对比方式</div>
                <div class="flex items-center gap-4">
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      type="radio"
                      value="mom"
                      v-model="compareType"
                      class="accent-[#165DFF]"
                    />
                    环比
                  </label>
                  <label
                    class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]"
                  >
                    <input
                      type="radio"
                      value="yoy"
                      v-model="compareType"
                      class="accent-[#165DFF]"
                    />
                    同比
                  </label>
                </div>
              </div>
              <div class="text-[11px] leading-5 text-[#939495]">
                当前为最小可用版本：仅支持基于当前查询结果做同/环比分析。支持年/季度/月/日；按周时分析入口已禁用。
              </div>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasRawResult || executing || isCompareAnalysisDisabled"
                  @click="runCompareAnalysis"
                  :title="isCompareAnalysisDisabled ? '同环比结果分析暂不支持按周' : ''"
                >
                  开始分析
                </button>
                <button
                  type="button"
                  class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                  :disabled="!hasAnalyzedResult"
                  @click="cancelResultAnalysis"
                >
                  取消分析
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </template>
  </div>
  <!-- 折叠时的竖向标签 -->
  <div
    v-if="isChartPanelCollapsed"
    class="flex flex-col items-center border-r border-[#e3e5eb] bg-white shrink-0 w-10 pt-3 gap-1"
  >
    <div
      class="writing-vertical text-[12px] py-2.5 px-1 rounded cursor-pointer transition-all select-none text-[#165DFF] font-medium bg-[#E8F3FF]"
      @click="openChartPanel"
    >
      图表展示
    </div>
    <div
      class="writing-vertical text-[12px] py-2.5 px-1 rounded cursor-pointer transition-all select-none text-[#57585a] hover:text-[#57585a] hover:bg-[#f0f1f3]"
      @click="openAnalysisPanel"
    >
      结果分析
    </div>
  </div>
</template>
