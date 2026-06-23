<script setup lang="ts">
import { ref } from 'vue'

interface HelpSection {
  title: string
  icon: string
  accentClass: string
  iconClass: string
  basedOn: string
  workflow: string[]
  fitFor: string
  basicCapabilities: string[]
  advancedCapabilities: string[]
  notFitFor: string
}

const visible = ref(false)

const sections: HelpSection[] = [
  {
    title: '数据智能体',
    icon: 'fa-solid fa-robot',
    accentClass: 'border-blue-200 bg-blue-50 text-blue-700',
    iconClass: 'bg-blue-100 text-blue-600',
    basedOn: '指标集',
    workflow: ['配置数据源', '维护字段字典', '配置表关系', '配置指标维度', '维护业务说明', '自然语言问数'],
    fitFor: '自然语言问数、快速理解业务问题、自动生成图表/看板/报告',
    basicCapabilities: ['指标维度查询', '明细数据查询', '已配置表关系查询', '自动选择指标维度'],
    advancedCapabilities: ['多轮追问', '自动图表生成', '保存为看板', '报告生成', '深度探索分析'],
    notFitFor: '临时修改指标口径、手写复杂 SQL、跨库查询或子查询',
  },
  {
    title: '可视化查询',
    icon: 'fa-solid fa-chart-column',
    accentClass: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    iconClass: 'bg-emerald-100 text-emerald-600',
    basedOn: '指标集',
    workflow: ['配置数据源', '维护字段字典', '配置表关系', '配置指标维度', '拖拽查询'],
    fitFor: '拖拽组装查询、手动调整聚合函数、配置筛选、排序和展示字段',
    basicCapabilities: ['指标维度查询', '明细数据查询', '已配置表关系查询', '手动选择配置'],
    advancedCapabilities: ['调整聚合函数', '配置筛选排序', '自定义图表展示', '保存为看板'],
    notFitFor: '自然语言推理、手写复杂 SQL、跨库查询或子查询',
  },
  {
    title: 'SQL查询',
    icon: 'fa-solid fa-code',
    accentClass: 'border-slate-200 bg-slate-50 text-slate-700',
    iconClass: 'bg-slate-200 text-slate-600',
    basedOn: '当前数据源',
    workflow: ['配置数据源', '选择数据源', '编写 SQL', '执行查询', '查看结果'],
    fitFor: '专业 SQL 查询、复杂查询条件、数据库原生语法查询',
    basicCapabilities: ['明细查询', '聚合查询', '多表关联查询', '计算表达式'],
    advancedCapabilities: ['复杂 SQL', '子查询', '跨库查询', '数据库原生函数', '保存为看板'],
    notFitFor: '自动理解业务指标、自动选择指标维度、直接生成看板或报告',
  },
]
</script>

<template>
  <button
    class="w-8 h-8 flex items-center justify-center border border-slate-200 rounded-xl bg-white text-slate-500 cursor-pointer shadow-sm hover:text-slate-700 hover:bg-slate-50"
    title="查询能力说明"
    @click="visible = true"
  >
    <i class="fa-solid fa-circle-question text-[13px]"></i>
  </button>

  <el-drawer v-model="visible" direction="ltr" size="min(920px, 82vw)" append-to-body :with-header="false" class="query-capability-drawer">
    <div class="flex h-full flex-col bg-[#f8fafc]">
      <div class="flex h-12 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5">
        <div class="text-[15px] font-semibold text-slate-800">查询能力说明</div>
        <button type="button" class="query-help-close-btn" title="关闭" @click="visible = false">
          <i class="fa-solid fa-xmark text-[15px]"></i>
        </button>
      </div>

      <div class="min-h-0 flex-1 overflow-auto px-5 py-4">
        <div class="mb-3 rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-[13px] leading-5 text-slate-500">
          <span class="font-semibold text-slate-700">选择建议：</span>
          自然语言用数据智能体，手工组合分析用可视化查询，复杂 SQL 或数据库原生语法用 SQL 查询。
        </div>

        <div class="space-y-3">
          <section
            v-for="section in sections"
            :key="section.title"
            class="rounded-lg border border-slate-200 bg-white p-4"
          >
            <div class="mb-3 flex items-center justify-between gap-3">
              <div class="flex min-w-0 items-center gap-3">
                <div :class="['flex h-9 w-9 shrink-0 items-center justify-center rounded-lg', section.iconClass]">
                  <i :class="[section.icon, 'text-[14px]']"></i>
                </div>
                <div class="min-w-0">
                  <div class="text-[15px] font-semibold text-slate-800">{{ section.title }}</div>
                </div>
              </div>
              <span :class="['shrink-0 rounded-full border px-2.5 py-1 text-[12px]', section.accentClass]">
                基于：{{ section.basedOn }}
              </span>
            </div>

            <div class="grid gap-3 text-[13px] leading-5">
              <div>
                <div class="mb-1.5 font-semibold text-slate-700">工作方式</div>
                <div class="flex flex-wrap items-center gap-2">
                  <template v-for="(step, index) in section.workflow" :key="step">
                    <div class="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-[12px] font-medium text-slate-600">
                      {{ step }}
                    </div>
                    <i v-if="index < section.workflow.length - 1" class="fa-solid fa-arrow-right text-[11px] text-slate-300"></i>
                  </template>
                </div>
              </div>

              <div class="rounded-md bg-slate-50 px-3 py-2">
                <span class="font-semibold text-slate-700">适合：</span>
                <span class="text-slate-600">{{ section.fitFor }}</span>
              </div>

              <div class="rounded-md bg-slate-50 px-3 py-2">
                <span class="font-semibold text-slate-700">不适合：</span>
                <span class="text-slate-500">{{ section.notFitFor }}</span>
              </div>

              <div>
                <div class="mb-1.5 font-semibold text-slate-700">基础能力</div>
                <div class="grid grid-cols-2 gap-2">
                  <div
                    v-for="item in section.basicCapabilities"
                    :key="item"
                    class="flex items-center gap-2 rounded-md border border-slate-100 bg-white px-3 py-2 text-slate-600"
                  >
                    <i class="fa-solid fa-check text-[11px] text-emerald-500"></i>
                    <span>{{ item }}</span>
                  </div>
                </div>
              </div>

              <div>
                <div class="mb-1.5 font-semibold text-slate-700">高级能力</div>
                <div class="grid grid-cols-2 gap-2">
                  <div
                    v-for="item in section.advancedCapabilities"
                    :key="item"
                    class="flex items-center gap-2 rounded-md border border-slate-100 bg-white px-3 py-2 text-slate-600"
                  >
                    <i class="fa-solid fa-check text-[11px] text-emerald-500"></i>
                    <span>{{ item }}</span>
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>

        <div class="mt-4 rounded-lg border border-slate-200 bg-white px-4 py-3 text-[12px] leading-5 text-slate-500">
          数据智能体和可视化查询基于指标集工作；SQL 查询直接面向当前数据源，可使用当前数据源支持的 SQL 语法。
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<style>
.query-capability-drawer .el-drawer__body {
  padding: 0;
}

.query-help-close-btn {
  display: inline-flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 9999px;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  outline: none;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.query-help-close-btn:hover {
  background: #f1f5f9;
  color: #475569;
}

.query-help-close-btn:focus-visible {
  box-shadow: 0 0 0 2px rgba(22, 119, 255, 0.18);
}
</style>
