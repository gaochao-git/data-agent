<script setup lang="ts">
import { computed, toRef, watch } from 'vue'
import { useSemanticEvaluation, type EvaluationCaseRunRow } from '../../composables/useSemanticEvaluation'
import type { AccuracyEvaluationRunCaseHistoryItem, AccuracyEvaluationRunHistoryItem } from '../../api'
import type { SemanticConfigItem } from '../../types'

const props = defineProps<{
  datasourceId: string
  config: SemanticConfigItem | null
  hasUnsavedSemanticConfig: boolean
  adminMode?: boolean
}>()

const visible = defineModel<boolean>('visible', { default: false })
const activeConfigRef = computed(() => props.config)
const hasUnsavedSemanticConfigRef = computed(() => props.hasUnsavedSemanticConfig)
const {
  draftingCase,
  editingCase,
  editingCaseDslError,
  editingCaseDslText,
  evaluateDrawerTab,
  evaluationActiveSummary,
  evaluationAllSelected,
  evaluationBusinessDomain,
  evaluationCases,
  evaluationCasesDirty,
  evaluationCasesLoading,
  evaluationCasesSaving,
  evaluationCaseStats,
  evaluationForm,
  evaluationHistoryCaseResult,
  evaluationHistoryCases,
  evaluationHistoryDetailLoading,
  evaluationHistoryLoading,
  evaluationHistoryPage,
  evaluationHistoryPageSize,
  evaluationHistoryRuns,
  evaluationHistoryTesterName,
  evaluationHistoryTotal,
  evaluationMetricTipByLabel,
  evaluationMetricTips,
  evaluationResultFilter,
  evaluationRunProgress,
  evaluationRunning,
  evaluationRunRows,
  evaluationRunRowsFiltered,
  evaluationRunStatusClass,
  evaluationRunStatusLabel,
  evaluationSelectedCount,
  evaluationSlotScoreItems,
  evaluationSummaryCards,
  evaluationToolTraceItems,
  formatEvaluationBooleanRate,
  formatEvaluationPercent,
  formatEvaluationTime,
  formatJsonPreview,
  generateEvaluationCasesDraft,
  handleEvaluationHistoryPageChange,
  handleEvaluationTabChange,
  loadEvaluationCases,
  loadEvaluationHistoryDetail,
  loadEvaluationHistoryRuns,
  openEditCaseEditor,
  openEvaluationDrawer,
  openEvaluationRunDetail,
  openNewCaseEditor,
  removeEvaluationCase,
  runActiveEvaluation,
  saveCaseEditor,
  saveEvaluationCases,
  selectedEvaluationHistoryCase,
  selectedEvaluationHistoryCaseId,
  selectedEvaluationHistoryRun,
  selectedEvaluationHistoryRunId,
  selectedEvaluationResult,
  selectedEvaluationResultId,
  showCaseEditorDialog,
  stopEvaluationRun,
  draftEditingCaseByLlm,
} = useSemanticEvaluation({
  datasourceId: toRef(props, 'datasourceId'),
  activeConfig: activeConfigRef,
  hasUnsavedSemanticConfig: hasUnsavedSemanticConfigRef,
  adminMode: toRef(props, 'adminMode'),
})

const drawerTitle = computed(() => props.config?.name || '当前语义模型')

watch(visible, async (next) => {
  if (!next) return
  await openEvaluationDrawer()
})

watch(evaluateDrawerTab, handleEvaluationTabChange)
</script>

<template>
  <el-drawer v-model="visible" class="chatdata-evaluation-drawer" direction="ltr" size="82%" :with-header="false" append-to-body :z-index="4200">
    <div class="h-full min-h-0 bg-[#f5f7fb] flex flex-col">
      <div class="h-12 px-4 border-b border-gray-200 bg-white flex items-center justify-between shrink-0">
        <div class="min-w-0">
          <div class="text-sm font-semibold text-gray-800">智能问数评测 - {{ drawerTitle }}</div>
        </div>
        <div class="flex items-center gap-2">
          <button class="w-8 h-8 cursor-pointer border border-gray-200 rounded-md bg-white text-gray-500 hover:border-gray-300 hover:bg-gray-50 hover:text-gray-800" @click="visible = false">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      </div>

      <div class="px-4 pt-3 bg-white border-b border-gray-200 shrink-0">
        <el-segmented
          v-model="evaluateDrawerTab"
          :options="[
            { label: '1. 测试集', value: 'cases' },
            { label: '2. 评测', value: 'run' },
            { label: '3. 历史', value: 'history' },
          ]"
        />
        <div class="py-2 text-xs text-gray-500">
          每个语义模型需先维护专属测试集：根据当前指标/维度生成草稿 → 校对并保存 → 再跑接口链路评测。
          <span v-if="evaluationCasesDirty" class="text-amber-600 ml-1">（有未保存修改）</span>
        </div>
      </div>

      <div class="flex-1 min-h-0 p-4 overflow-auto">
        <div v-if="evaluateDrawerTab === 'cases'" class="bg-white border border-gray-200 rounded-lg overflow-hidden">
          <div class="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center justify-between gap-2">
            <div class="flex items-center gap-4 text-xs text-gray-600">
              <span>共 {{ evaluationCaseStats.total }} 条</span>
              <span>人工 {{ evaluationCaseStats.human }}</span>
              <span>LLM {{ evaluationCaseStats.llm }}</span>
              <span v-if="evaluationBusinessDomain">业务域 {{ evaluationBusinessDomain }}</span>
            </div>
            <div class="flex items-center gap-2">
              <button
                class="px-3 py-1.5 cursor-pointer rounded-md border border-gray-200 bg-white text-xs text-gray-700 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-60"
                :disabled="evaluationCasesLoading"
                @click="loadEvaluationCases"
              >
                刷新
              </button>
              <button
                class="px-3 py-1.5 cursor-pointer rounded-md border border-[#1677FF] bg-[#E6F4FF] text-xs text-[#1677FF] hover:bg-[#D8ECFF] disabled:cursor-not-allowed disabled:opacity-60"
                :disabled="evaluationCasesLoading"
                @click="generateEvaluationCasesDraft"
              >
                <i :class="evaluationCasesLoading ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-wand-magic-sparkles mr-1'"></i>生成测试集草稿
              </button>
              <button
                class="px-3 py-1.5 cursor-pointer rounded-md border-none bg-[#1677FF] text-xs text-white hover:bg-[#0958D9] disabled:cursor-not-allowed disabled:opacity-60"
                :disabled="evaluationCasesSaving || evaluationCases.length === 0 || !evaluationCasesDirty"
                :title="evaluationCasesDirty ? '保存测试集修改' : '测试集没有未保存修改'"
                @click="saveEvaluationCases"
              >
                <i :class="evaluationCasesSaving ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-floppy-disk mr-1'"></i>保存测试集
              </button>
              <button
                class="px-3 py-1.5 cursor-pointer rounded-md border border-gray-200 bg-white text-xs text-gray-700 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-600"
                @click="openNewCaseEditor"
              >
                新增用例
              </button>
            </div>
          </div>
          <el-table v-loading="evaluationCasesLoading" :data="evaluationCases" height="620" size="small" border>
            <el-table-column prop="id" label="用例 ID" min-width="220" show-overflow-tooltip />
            <el-table-column prop="nl" label="自然语言问题" min-width="240" show-overflow-tooltip />
            <el-table-column prop="difficulty" label="难度" width="80" />
            <el-table-column label="来源" width="80">
              <template #default="{ row }">{{ row.source === 'llm' ? 'LLM' : row.source === 'human' ? '人工' : row.source }}</template>
            </el-table-column>
            <el-table-column label="标准 SQL" width="90">
              <template #default="{ row }">{{ row.gold_sql ? '已生成' : '待生成' }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ $index }">
                <el-button type="primary" link size="small" @click.stop="openEditCaseEditor($index)">编辑</el-button>
                <el-button type="danger" link size="small" @click.stop="removeEvaluationCase($index)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="!evaluationCasesLoading && evaluationCases.length === 0" class="py-16 text-center text-sm text-gray-400">
            当前语义模型还没有测试集。请点击「生成测试集草稿」，系统会按本语义模型的指标、维度和表关系自动生成评测用例并生成标准 SQL。
          </div>
        </div>

        <div v-else-if="evaluateDrawerTab === 'run'" class="flex flex-col gap-4 min-h-full">
          <div class="flex flex-wrap items-center justify-between gap-3 text-xs text-gray-600">
            <div class="flex flex-wrap items-center gap-3">
              <span>超时</span>
              <el-input-number v-model="evaluationForm.request_timeout" :min="10" :max="600" size="small" style="width:100px" />
              <span>秒</span>
              <el-checkbox v-model="evaluationForm.skip_execution" size="small">跳过 SQL 执行</el-checkbox>
              <el-checkbox v-model="evaluationForm.include_traces" size="small">工具轨迹</el-checkbox>
              <span class="text-gray-400 ml-1">保存工具调用明细用于排查；评测始终按用例流式更新</span>
            </div>
            <div class="flex items-center gap-2">
              <button
                v-if="evaluationRunning"
                class="px-3 py-1.5 rounded-md border border-red-200 bg-white text-xs text-red-600 hover:bg-red-50"
                @click="stopEvaluationRun"
              >
                <i class="fa-solid fa-stop mr-1"></i>停止评测
              </button>
              <button
                v-else
                class="px-3 py-1.5 rounded-md border-none bg-[#1677FF] text-xs text-white cursor-pointer transition-all duration-150 hover:bg-[#0958D9] active:bg-[#003EB3] focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-[#1677FF] disabled:active:bg-[#1677FF]"
                :disabled="evaluationSelectedCount === 0"
                :title="evaluationSelectedCount === 0 ? '请勾选要运行的用例' : '运行选中的评测用例'"
                @click="runActiveEvaluation"
              >
                <i class="fa-solid fa-play mr-1"></i>运行选中 ({{ evaluationSelectedCount }})
              </button>
            </div>
          </div>

          <div v-if="evaluationActiveSummary || evaluationRunning" class="grid grid-cols-6 gap-3">
            <div
              v-for="card in evaluationSummaryCards"
              :key="card.label"
              class="bg-white border border-gray-200 rounded-lg px-3 py-3"
              :class="card.tone === 'success' ? 'border-emerald-200' : card.tone === 'danger' ? 'border-red-200' : card.tone === 'primary' ? 'border-blue-200' : ''"
            >
              <div class="text-xs text-gray-500">
                <span class="evaluation-metric-header">
                  {{ card.label }}
                  <el-tooltip v-if="evaluationMetricTipByLabel[card.label]" :content="evaluationMetricTipByLabel[card.label]" placement="top" popper-class="chatdata-evaluation-tooltip">
                    <i class="fa-regular fa-circle-question"></i>
                  </el-tooltip>
                </span>
              </div>
              <div
                class="mt-1 text-lg font-semibold"
                :class="card.tone === 'success' ? 'text-emerald-700' : card.tone === 'danger' ? 'text-red-600' : card.tone === 'primary' ? 'text-[#1677FF]' : 'text-gray-800'"
              >
                {{ card.value }}
              </div>
            </div>
          </div>

          <div class="grid grid-cols-[minmax(560px,1.05fr)_minmax(440px,0.95fr)] gap-4 min-h-[620px]">
            <div class="bg-white border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
              <div class="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3 shrink-0">
                <div class="flex items-center gap-3">
                  <span class="text-xs text-gray-500">已选 {{ evaluationSelectedCount }} / {{ evaluationRunRows.length }} 条</span>
                  <el-segmented
                    v-model="evaluationResultFilter"
                    size="small"
                    :options="[
                      { label: '全部', value: 'all' },
                      { label: '通过', value: 'passed' },
                      { label: '未通过', value: 'failed' },
                    ]"
                  />
                </div>
                <div class="flex items-center gap-3 text-xs text-gray-500">
                  <span v-if="evaluationRunning || evaluationRunProgress.total > 0">
                    进度 {{ evaluationRunProgress.current }} / {{ evaluationRunProgress.total }}
                  </span>
                  <span v-if="evaluationRunning" class="text-[#1677FF]">
                    <i class="fa-solid fa-spinner fa-spin mr-1"></i>流式评测中…
                  </span>
                </div>
              </div>

              <el-table
                :data="evaluationRunRowsFiltered"
                height="560"
                size="small"
                border
                highlight-current-row
                row-key="id"
                :current-row-key="selectedEvaluationResultId"
                @row-click="(row: EvaluationCaseRunRow) => openEvaluationRunDetail(row)"
              >
                <el-table-column width="44" align="center">
                  <template #header>
                    <el-checkbox v-model="evaluationAllSelected" :disabled="evaluationRunning" />
                  </template>
                  <template #default="{ row }">
                    <el-checkbox v-model="row.selected" :disabled="evaluationRunning" @click.stop />
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="92" align="center">
                  <template #default="{ row }">
                    <span class="text-[11px] px-2 py-0.5 rounded inline-flex items-center gap-1" :class="evaluationRunStatusClass(row.runStatus)">
                      <i v-if="row.runStatus === 'running'" class="fa-solid fa-spinner fa-spin"></i>
                      {{ evaluationRunStatusLabel(row.runStatus) }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column prop="id" label="用例 ID" min-width="220" show-overflow-tooltip />
                <el-table-column prop="nl" label="自然语言问题" min-width="180" show-overflow-tooltip />
                <el-table-column width="104" align="center">
                  <template #header>
                    <span class="evaluation-metric-header">
                      语义得分
                      <el-tooltip :content="evaluationMetricTips.semantic" placement="top" popper-class="chatdata-evaluation-tooltip">
                        <i class="fa-regular fa-circle-question"></i>
                      </el-tooltip>
                    </span>
                  </template>
                  <template #default="{ row }">
                    {{ row.runResult ? formatEvaluationPercent(row.runResult.semantic_accuracy) : '—' }}
                  </template>
                </el-table-column>
                <el-table-column width="104" align="center">
                  <template #header>
                    <span class="evaluation-metric-header">
                      结构匹配
                      <el-tooltip :content="evaluationMetricTips.structure" placement="top" popper-class="chatdata-evaluation-tooltip">
                        <i class="fa-regular fa-circle-question"></i>
                      </el-tooltip>
                    </span>
                  </template>
                  <template #default="{ row }">
                    {{ row.runResult ? formatEvaluationBooleanRate(row.runResult.dsl_exact_match) : '—' }}
                  </template>
                </el-table-column>
                <el-table-column width="96" align="center">
                  <template #header>
                    <span class="evaluation-metric-header">
                      SQL匹配
                      <el-tooltip :content="evaluationMetricTips.sql" placement="top" popper-class="chatdata-evaluation-tooltip">
                        <i class="fa-regular fa-circle-question"></i>
                      </el-tooltip>
                    </span>
                  </template>
                  <template #default="{ row }">
                    {{ row.runResult ? formatEvaluationBooleanRate(row.runResult.sql_exact_match) : '—' }}
                  </template>
                </el-table-column>
                <el-table-column label="耗时" width="74" align="center">
                  <template #default="{ row }">
                    {{ row.runResult?.elapsed_ms != null ? `${(row.runResult.elapsed_ms / 1000).toFixed(1)}秒` : '—' }}
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <div class="bg-white border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
              <div class="px-4 py-3 border-b border-gray-100 shrink-0">
                <div class="text-sm font-semibold text-gray-800 truncate">
                  {{ selectedEvaluationResult?.question || selectedEvaluationResultId || '评测详情' }}
                </div>
                <div v-if="selectedEvaluationResult" class="mt-2 flex flex-wrap gap-2">
                  <span
                    v-for="slot in evaluationSlotScoreItems(selectedEvaluationResult)"
                    :key="slot.key"
                    class="text-[11px] px-2 py-0.5 rounded"
                    :class="slot.score >= 1 ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'"
                    :title="slot.score >= 1 ? '通过' : '未通过'"
                  >
                    {{ slot.key }}
                  </span>
                </div>
              </div>

              <div v-if="selectedEvaluationResult" class="flex-1 min-h-0 overflow-auto bg-[#fafbfc] p-4">
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">标准语义结构</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationResult.expected_dsl || selectedEvaluationResult.gold_dsl) }}</pre>
                  </div>
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">模型生成语义结构</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationResult.predicted_dsl) }}</pre>
                  </div>
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">标准 SQL</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationResult.gold_sql || '—' }}</pre>
                  </div>
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">生成 SQL</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationResult.generated_sql || selectedEvaluationResult.agent_tool_sql || '—' }}</pre>
                  </div>
                </div>
                <div v-if="selectedEvaluationResult.error" class="mt-3 rounded border border-red-100 bg-red-50 p-2 text-xs text-red-600 whitespace-pre-wrap">
                  {{ selectedEvaluationResult.error }}
                </div>
                <div v-if="evaluationToolTraceItems(selectedEvaluationResult).length" class="mt-3">
                  <el-collapse>
                    <el-collapse-item :title="`工具轨迹（${evaluationToolTraceItems(selectedEvaluationResult).length}）`" name="tool-traces">
                      <div class="space-y-2">
                        <div
                          v-for="(trace, traceIndex) in evaluationToolTraceItems(selectedEvaluationResult)"
                          :key="traceIndex"
                          class="rounded border border-gray-200 bg-white p-2"
                        >
                          <div class="mb-1 flex items-center gap-2 text-xs">
                            <span class="font-semibold text-gray-700">{{ trace.name || `tool_${traceIndex + 1}` }}</span>
                            <span class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">{{ trace.status || '-' }}</span>
                          </div>
                          <div class="grid grid-cols-2 gap-2">
                            <pre class="max-h-28 overflow-auto rounded bg-gray-50 p-2 text-[11px]">{{ formatJsonPreview(trace.args) }}</pre>
                            <pre class="max-h-28 overflow-auto rounded bg-gray-50 p-2 text-[11px] whitespace-pre-wrap">{{ trace.content || '—' }}</pre>
                          </div>
                        </div>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </div>
              </div>
              <div v-else class="flex-1 flex items-center justify-center bg-[#fafbfc] px-6 text-center text-xs text-gray-400">
                勾选用例并点击「运行选中」，完成后点击左侧用例查看 DSL/SQL 对比详情
              </div>
            </div>
          </div>
        </div>

        <div v-else class="flex flex-col gap-4 min-h-full">
          <div class="border border-gray-200 rounded-lg overflow-hidden flex flex-col bg-white shrink-0">
            <div class="px-3 py-2 border-b border-gray-100 flex items-center justify-between shrink-0">
              <span class="text-sm font-semibold text-gray-700">运行批次</span>
              <button class="px-2.5 py-1 rounded border border-gray-200 bg-white text-xs text-gray-600" :disabled="evaluationHistoryLoading" @click="loadEvaluationHistoryRuns">
                <i :class="evaluationHistoryLoading ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-rotate-right mr-1'"></i>刷新
              </button>
            </div>
            <el-table
              v-loading="evaluationHistoryLoading"
              :data="evaluationHistoryRuns"
              height="220"
              size="small"
              border
              highlight-current-row
              row-key="run_id"
              :current-row-key="selectedEvaluationHistoryRunId"
              @row-click="(row: AccuracyEvaluationRunHistoryItem) => loadEvaluationHistoryDetail(row.run_id)"
            >
              <el-table-column label="时间" min-width="160">
                <template #default="{ row }">{{ formatEvaluationTime(row.started_time || row.create_time) }}</template>
              </el-table-column>
              <el-table-column label="测试人员" min-width="120" show-overflow-tooltip>
                <template #default="{ row }">{{ evaluationHistoryTesterName(row) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="110" align="center">
                <template #default="{ row }">{{ row.run_status }}</template>
              </el-table-column>
              <el-table-column label="语义得分" width="110" align="center">
                <template #default="{ row }">{{ formatEvaluationPercent(row.semantic_accuracy) }}</template>
              </el-table-column>
              <el-table-column label="通过" width="90" align="center">
                <template #default="{ row }">{{ row.passed_count }}</template>
              </el-table-column>
              <el-table-column label="未通过" width="90" align="center">
                <template #default="{ row }">{{ row.failed_count }}</template>
              </el-table-column>
              <el-table-column label="用例数" width="90" align="center">
                <template #default="{ row }">{{ row.scored_count || row.total_count }}</template>
              </el-table-column>
              <el-table-column prop="run_id" label="运行 ID" min-width="260" show-overflow-tooltip />
            </el-table>
            <div class="px-3 py-2 border-t border-gray-100 shrink-0">
              <el-pagination
                size="small"
                layout="prev, pager, next"
                :current-page="evaluationHistoryPage"
                :page-size="evaluationHistoryPageSize"
                :total="evaluationHistoryTotal"
                @current-change="handleEvaluationHistoryPageChange"
              />
            </div>
          </div>

          <div class="grid grid-cols-[minmax(420px,0.8fr)_minmax(620px,1.2fr)] gap-4 min-h-[440px] flex-1">
            <div class="border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0 bg-white">
              <div class="px-3 py-2 border-b border-gray-100 text-sm font-semibold text-gray-700 shrink-0">
                用例结果
                <span v-if="selectedEvaluationHistoryRun" class="ml-2 text-xs font-normal text-gray-400">
                  {{ selectedEvaluationHistoryRun.passed_count }} 通过 / {{ selectedEvaluationHistoryRun.failed_count }} 未通过
                </span>
              </div>
              <el-table
                v-loading="evaluationHistoryDetailLoading"
                :data="evaluationHistoryCases"
                height="390"
                size="small"
                border
                highlight-current-row
                row-key="case_id"
                :current-row-key="selectedEvaluationHistoryCaseId"
                @row-click="(row: AccuracyEvaluationRunCaseHistoryItem) => selectedEvaluationHistoryCaseId = row.case_id"
              >
                <el-table-column label="状态" width="76" align="center">
                  <template #default="{ row }">{{ row.case_status === 'passed' ? '通过' : '未通过' }}</template>
                </el-table-column>
                <el-table-column prop="case_id" label="用例 ID" min-width="220" show-overflow-tooltip />
                <el-table-column width="104" align="center">
                  <template #header>
                    <span class="evaluation-metric-header">
                      语义得分
                      <el-tooltip :content="evaluationMetricTips.semantic" placement="top" popper-class="chatdata-evaluation-tooltip">
                        <i class="fa-regular fa-circle-question"></i>
                      </el-tooltip>
                    </span>
                  </template>
                  <template #default="{ row }">{{ formatEvaluationPercent(row.semantic_accuracy) }}</template>
                </el-table-column>
                <el-table-column width="104" align="center">
                  <template #header>
                    <span class="evaluation-metric-header">
                      结构匹配
                      <el-tooltip :content="evaluationMetricTips.structure" placement="top" popper-class="chatdata-evaluation-tooltip">
                        <i class="fa-regular fa-circle-question"></i>
                      </el-tooltip>
                    </span>
                  </template>
                  <template #default="{ row }">{{ formatEvaluationBooleanRate(evaluationHistoryCaseResult(row).dsl_exact_match) }}</template>
                </el-table-column>
                <el-table-column width="96" align="center">
                  <template #header>
                    <span class="evaluation-metric-header">
                      SQL匹配
                      <el-tooltip :content="evaluationMetricTips.sql" placement="top" popper-class="chatdata-evaluation-tooltip">
                        <i class="fa-regular fa-circle-question"></i>
                      </el-tooltip>
                    </span>
                  </template>
                  <template #default="{ row }">{{ formatEvaluationBooleanRate(evaluationHistoryCaseResult(row).sql_exact_match) }}</template>
                </el-table-column>
                <el-table-column label="耗时" width="76" align="center">
                  <template #default="{ row }">{{ row.elapsed_ms != null ? `${(row.elapsed_ms / 1000).toFixed(1)}秒` : '—' }}</template>
                </el-table-column>
              </el-table>
            </div>

            <div class="border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0 bg-white">
              <div class="px-4 py-3 border-b border-gray-100 shrink-0">
                <div class="text-sm font-semibold text-gray-800 truncate">
                  {{ selectedEvaluationHistoryCase?.question || selectedEvaluationHistoryCaseId || '评测详情' }}
                </div>
                <div v-if="selectedEvaluationHistoryCase" class="mt-2 flex flex-wrap gap-2">
                  <span
                    v-for="slot in evaluationSlotScoreItems(selectedEvaluationHistoryCase)"
                    :key="slot.key"
                    class="text-[11px] px-2 py-0.5 rounded"
                    :class="slot.score >= 1 ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'"
                    :title="slot.score >= 1 ? '通过' : '未通过'"
                  >
                    {{ slot.key }}
                  </span>
                </div>
              </div>
              <div v-if="selectedEvaluationHistoryCase" class="flex-1 min-h-0 overflow-auto bg-[#fafbfc] p-4">
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">标准语义结构</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationHistoryCase.expected_dsl || selectedEvaluationHistoryCase.gold_dsl) }}</pre>
                  </div>
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">模型生成语义结构</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationHistoryCase.predicted_dsl) }}</pre>
                  </div>
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">标准 SQL</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationHistoryCase.gold_sql || '—' }}</pre>
                  </div>
                  <div>
                    <div class="text-xs font-medium text-gray-600 mb-1">生成 SQL</div>
                    <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationHistoryCase.generated_sql || selectedEvaluationHistoryCase.agent_tool_sql || '—' }}</pre>
                  </div>
                </div>
                <div v-if="selectedEvaluationHistoryCase.error" class="mt-3 rounded border border-red-100 bg-red-50 p-2 text-xs text-red-600 whitespace-pre-wrap">
                  {{ selectedEvaluationHistoryCase.error }}
                </div>
              </div>
              <div v-else class="flex-1 flex items-center justify-center bg-[#fafbfc] px-6 text-center text-xs text-gray-400">
                选择左侧评测批次和用例查看详情
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>

  <el-dialog v-model="showCaseEditorDialog" width="720px" title="编辑评测用例" append-to-body :z-index="4300">
    <el-form label-position="top" size="small">
      <div class="grid grid-cols-2 gap-3">
        <el-form-item label="用例 ID">
          <el-input v-model="editingCase.id" />
        </el-form-item>
        <el-form-item label="业务域">
          <el-input v-model="editingCase.business_domain" disabled />
        </el-form-item>
      </div>
      <el-form-item label="自然语言问题">
        <div class="w-full">
          <el-input v-model="editingCase.nl" type="textarea" :rows="3" placeholder="请输入自然语言问题，也可以直接粘贴 SQL 生成标准语义结构" />
        </div>
      </el-form-item>
      <div class="grid grid-cols-2 gap-3">
        <el-form-item label="难度">
          <el-select v-model="editingCase.difficulty" class="w-full">
            <el-option label="easy" value="easy" />
            <el-option label="medium" value="medium" />
            <el-option label="hard" value="hard" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源">
          <el-select v-model="editingCase.source" class="w-full">
            <el-option label="人工" value="human" />
            <el-option label="LLM" value="llm" />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item>
        <template #label>
          <div class="flex items-center gap-2">
            <span>标准语义结构（JSON）</span>
            <button
              type="button"
              class="h-7 px-2.5 rounded border border-[#1677FF] bg-[#E6F4FF] text-xs text-[#1677FF] disabled:opacity-60"
              :disabled="draftingCase"
              @click="draftEditingCaseByLlm"
            >
              <i :class="draftingCase ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-wand-magic-sparkles mr-1'"></i>LLM 生成语义结构
            </button>
          </div>
        </template>
        <el-input v-model="editingCaseDslText" type="textarea" :rows="10" class="font-mono text-xs" />
        <div v-if="editingCaseDslError" class="mt-1 text-xs text-red-500">
          {{ editingCaseDslError }}
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <button class="px-3 py-1.5 border border-gray-200 rounded bg-white text-xs text-gray-600" @click="showCaseEditorDialog = false">取消</button>
      <button class="ml-2 px-3 py-1.5 border-none rounded bg-[#1677FF] text-xs text-white" @click="saveCaseEditor">确定</button>
    </template>
  </el-dialog>
</template>

<style scoped>
:global(.chatdata-evaluation-drawer .el-drawer__body) {
  padding: 0;
  height: 100%;
}

:global(.chatdata-evaluation-tooltip) {
  max-width: 300px;
  line-height: 1.5;
  font-size: 12px;
}

.evaluation-metric-header {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.evaluation-metric-header i {
  font-size: 12px;
  color: #9ca3af;
  cursor: help;
}

.evaluation-metric-header i:hover {
  color: #1677ff;
}
</style>
