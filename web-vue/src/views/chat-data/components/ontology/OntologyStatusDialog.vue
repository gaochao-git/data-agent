<script setup lang="ts">
/**
 * 本体对象状态迁移弹窗：升级（draft→experimental→certified）/ 降级 / 废弃。
 * 废弃为终态不可逆：必填原因，可选替代对象与删除期限；认证前置条件
 * （≥1 标识属性 + ≥1 绑定）由后端校验，这里做基础提示。
 */
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { ElMessage } from 'element-plus'
import { changeAdminOntologyObjectStatus } from '../../api'
import type { OntologyObjectItem, OntologyObjectStatus } from '../../types'
import { ONTOLOGY_STATUS_META } from './ontologyMeta'

const props = defineProps({
  // 迁移目标对象（列表行即可）
  object: { type: Object as PropType<OntologyObjectItem | null>, default: null },
  // 全部活跃对象：废弃时替代对象候选
  objects: { type: Array as PropType<OntologyObjectItem[]>, default: () => [] }
})

const emit = defineEmits<{ saved: [] }>()

const visible = defineModel<boolean>('visible', { default: false })

const saving = ref(false)
const targetStatus = ref<OntologyObjectStatus>('experimental')
const deprecationReason = ref('')
const deprecatedUntil = ref('')
const successorName = ref('')

const currentStatus = computed(() => props.object?.status || 'draft')

// 候选目标状态：deprecated 终态不可迁移；当前状态除外
const targetOptions = computed<OntologyObjectStatus[]>(() => {
  if (currentStatus.value === 'deprecated') return []
  return (['draft', 'experimental', 'certified', 'deprecated'] as OntologyObjectStatus[]).filter(s => s !== currentStatus.value)
})

const isDeprecating = computed(() => targetStatus.value === 'deprecated')

const successorOptions = computed(() => props.objects.filter(item => item.object_name !== props.object?.object_name))

watch(visible, value => {
  if (!value) return
  deprecationReason.value = ''
  deprecatedUntil.value = ''
  successorName.value = ''
  // 默认目标：按当前状态给出升级下一档
  targetStatus.value = currentStatus.value === 'draft' ? 'experimental' : currentStatus.value === 'experimental' ? 'certified' : 'experimental'
})

const confirmStatus = async () => {
  const target = targetStatus.value
  if (isDeprecating.value && !deprecationReason.value.trim()) {
    ElMessage.warning('废弃对象必须填写废弃原因')
    return
  }
  saving.value = true
  try {
    const res = await changeAdminOntologyObjectStatus(props.object!.object_name, {
      status: target,
      deprecation_reason: isDeprecating.value ? deprecationReason.value.trim() : undefined,
      deprecated_until: isDeprecating.value && deprecatedUntil.value ? deprecatedUntil.value : undefined,
      successor_object_name: isDeprecating.value && successorName.value ? successorName.value : undefined
    })
    if (res.status === 'success') {
      ElMessage.success(`状态已更新为 ${ONTOLOGY_STATUS_META[target].label}`)
      visible.value = false
      emit('saved')
    } else {
      ElMessage.error(res.msg || '状态变更失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如认证前置不满足），不再重复提示
    console.error('本体对象状态变更失败:', err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" width="min(520px, 94vw)" :append-to-body="true">
    <template #header>
      <span class="text-base font-medium text-slate-800">状态迁移：{{ object?.display_name || object?.object_name }}</span>
    </template>

    <div v-if="currentStatus === 'deprecated'" class="rounded-lg border border-red-100 bg-red-50 p-3 text-xs leading-5 text-red-600">
      <p class="font-medium"><i class="fa-solid fa-triangle-exclamation mr-1"></i>deprecated 为终态且不可逆</p>
      <p v-if="object?.deprecation_reason" class="mt-1">废弃原因：{{ object.deprecation_reason }}</p>
      <p v-if="object?.successor_object_name" class="mt-0.5">替代对象：{{ object.successor_object_name }}</p>
      <p v-if="object?.deprecated_until" class="mt-0.5">删除期限：{{ object.deprecated_until }}</p>
      <p class="mt-1">如需继续演进，请基于替代对象重建。</p>
    </div>

    <template v-else>
      <div class="flex flex-col gap-3">
        <div class="flex items-center gap-2 text-xs text-slate-500">
          <span>当前状态</span>
          <el-tag size="small" :type="ONTOLOGY_STATUS_META[currentStatus].tagType" effect="plain">
            {{ ONTOLOGY_STATUS_META[currentStatus].label }}
          </el-tag>
          <span>{{ ONTOLOGY_STATUS_META[currentStatus].hint }}</span>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">目标状态</div>
          <el-select v-model="targetStatus" size="small" :empty-values="[null, undefined]" class="!w-full">
            <el-option
              v-for="status in targetOptions"
              :key="status"
              :label="`${ONTOLOGY_STATUS_META[status].label}（${ONTOLOGY_STATUS_META[status].hint}）`"
              :value="status"
            />
          </el-select>
        </div>

        <div
          v-if="targetStatus === 'certified'"
          class="rounded-lg border border-blue-100 bg-blue-50 p-2.5 text-[11px] leading-5 text-blue-700"
        >
          认证前置：对象需至少一个标识属性（主键）且至少一个语义模型绑定；认证后主键/标题键冻结、禁止删除属性。
        </div>
        <div
          v-if="targetStatus === 'certified' && object && (object.bindings || []).length === 0"
          class="rounded-lg border border-amber-100 bg-amber-50 p-2.5 text-[11px] leading-5 text-amber-700"
        >
          当前对象暂无语义模型绑定，认证会被拒绝；请先在编辑页添加绑定。
        </div>

        <template v-if="isDeprecating">
          <div class="rounded-lg border border-red-100 bg-red-50 p-2.5 text-[11px] leading-5 text-red-600">
            废弃为终态且不可逆，请谨慎操作。关系状态将随对象自动级联为废弃。
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">废弃原因（必填）</div>
            <el-input v-model="deprecationReason" size="small" maxlength="512" placeholder="如：已被新对象 sales_order_v2 替代" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">替代对象（可选，需已存在）</div>
            <el-select
              v-model="successorName"
              size="small"
              filterable
              clearable
              :empty-values="[null, undefined]"
              placeholder="选择替代对象"
              class="!w-full"
            >
              <el-option
                v-for="item in successorOptions"
                :key="item.object_name"
                :label="`${item.display_name} (${item.object_name})`"
                :value="item.object_name"
              />
            </el-select>
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">删除期限（可选）</div>
            <el-date-picker
              v-model="deprecatedUntil"
              type="datetime"
              size="small"
              value-format="YYYY-MM-DD HH:mm:ss"
              placeholder="YYYY-MM-DD HH:mm:ss"
              class="!w-full"
            />
          </div>
        </template>
      </div>
    </template>

    <template #footer>
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button
        v-if="currentStatus !== 'deprecated'"
        type="primary"
        size="small"
        :loading="saving"
        @click="confirmStatus"
      >
        确认迁移
      </el-button>
    </template>
  </el-dialog>
</template>
