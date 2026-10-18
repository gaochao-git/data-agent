<script setup lang="ts">
/**
 * 本体一致性健康巡检弹窗：一键巡检「本体↔语义模型」失修清单。
 * 只巡检不阻断——产出失效维度映射/关系列/类型冲突/属性未映射，供管理员
 * 修正映射（映射编辑器）或恢复维度；报告形状与后端 findings 同构。
 */
import { ref, watch } from 'vue'
import { checkAdminOntologyConsistency } from '../../api'
import type { OntologyConsistencyFinding, OntologyConsistencyReport } from '../../api'

const visible = defineModel<boolean>('visible', { default: false })

const loading = ref(false)
const report = ref<OntologyConsistencyReport | null>(null)
const loadError = ref('')

const SEVERITY_META: Record<string, { label: string; tagType: 'danger' | 'warning' | 'info' }> = {
  error: { label: '错误', tagType: 'danger' },
  warning: { label: '警告', tagType: 'warning' },
  info: { label: '提示', tagType: 'info' }
}

const loadReport = async () => {
  loading.value = true
  loadError.value = ''
  try {
    const res = await checkAdminOntologyConsistency()
    if (res.status === 'success') {
      report.value = res.data
    } else {
      loadError.value = res.msg || '一致性检查失败'
    }
  } catch (err) {
    console.error('本体一致性检查失败:', err)
    loadError.value = '一致性检查失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(visible, value => {
  if (value) void loadReport()
})

const findingTargets = (finding: OntologyConsistencyFinding) => finding.targets.join(' · ')
</script>

<template>
  <el-dialog v-model="visible" width="min(760px, 94vw)" :append-to-body="true">
    <template #header>
      <span class="text-base font-medium text-slate-800">本体一致性检查</span>
    </template>

    <div v-loading="loading" class="min-h-40">
      <template v-if="loadError">
        <div class="rounded-lg border border-red-100 bg-red-50 p-3 text-xs text-red-600">{{ loadError }}</div>
      </template>
      <template v-else-if="report">
        <!-- 汇总条 -->
        <div class="mb-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">
          <span
            class="rounded-md px-2 py-1 font-medium"
            :class="report.healthy ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'"
          >
            {{ report.healthy ? '导航地图健康' : '存在失修条目' }}
          </span>
          <span>检查范围：{{ report.group_count }} 个数据源组 / {{ report.config_count }} 个公共模型 / {{ report.object_count }} 个对象</span>
          <span v-if="report.summary.error">错误 {{ report.summary.error }}</span>
          <span v-if="report.summary.warning">警告 {{ report.summary.warning }}</span>
          <span v-if="report.summary.info">提示 {{ report.summary.info }}</span>
          <el-button size="small" text type="primary" :loading="loading" @click="loadReport">重新检查</el-button>
        </div>

        <div
          v-if="report.healthy"
          class="rounded-lg border border-green-100 bg-green-50 p-3 text-xs leading-5 text-green-700"
        >
          本体对象、映射与关系均与当前公共语义模型一致，AI 导航地图可信。
        </div>

        <!-- findings 列表 -->
        <div v-else class="flex flex-col gap-2">
          <div
            v-for="(finding, index) in report.findings"
            :key="`${finding.code}-${index}`"
            class="rounded-lg border border-slate-100 bg-slate-50/60 p-2.5"
          >
            <div class="flex items-center gap-2">
              <el-tag size="small" :type="SEVERITY_META[finding.severity]?.tagType || 'info'" effect="plain">
                {{ SEVERITY_META[finding.severity]?.label || finding.severity }}
              </el-tag>
              <span class="text-[11px] text-slate-400">{{ finding.code }}</span>
              <span class="ml-auto truncate text-[11px] text-slate-400">{{ findingTargets(finding) }}</span>
            </div>
            <p class="mt-1 text-xs leading-5 text-slate-700">{{ finding.message }}</p>
          </div>
        </div>

        <p class="mt-3 text-[11px] leading-4 text-slate-400">
          修复方式：失效维度映射/类型冲突 → 在对象编辑页修正属性映射；关系列失效 → 修正关系定义或恢复维度。
          检查只读不阻断，修复后可重新检查确认。
        </p>
      </template>
    </div>

    <template #footer>
      <el-button size="small" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>
