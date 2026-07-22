<script setup lang="ts">
import type { PropType } from 'vue'
import type { AggregationField, DimensionField } from './types'

type DimensionDraft = DimensionField & { selected?: boolean }
type AggregationDraft = AggregationField & { selected?: boolean }

defineProps({
  dimensionDrafts: { type: Array as PropType<DimensionDraft[]>, required: true },
  aggregationDrafts: { type: Array as PropType<AggregationDraft[]>, required: true },
  transformOptions: { type: Array as PropType<Array<{ value: string; label: string }>>, required: true },
  aggFunctions: { type: Array as PropType<Array<{ value: string; label: string }>>, required: true },
  getDimensionDisplayName: {
    type: Function as PropType<(field: DimensionField) => string>,
    required: true,
  },
  getMetricDisplayName: {
    type: Function as PropType<(field: AggregationField) => string>,
    required: true,
  },
  isDimensionDateField: {
    type: Function as PropType<(field: Pick<DimensionField, 'table' | 'column'>) => boolean>,
    required: true,
  },
})

const showDimensionManager = defineModel<boolean>('showDimensionManager', { required: true })
const showAggregationManager = defineModel<boolean>('showAggregationManager', { required: true })

const emit = defineEmits<{
  removeSelectedDimensionDrafts: []
  moveDimensionDraft: [index: number, direction: -1 | 1]
  saveDimensionManager: []
  removeSelectedAggregationDrafts: []
  moveAggregationDraft: [index: number, direction: -1 | 1]
  saveAggregationManager: []
}>()
</script>

<template>
  <el-dialog
    v-model="showDimensionManager"
    width="760px"
    :close-on-click-modal="false"
    title="维度管理"
  >
    <div class="space-y-3">
      <div class="flex items-center justify-between">
        <div class="text-[12px] text-[#6b7280]">批量管理当前查询已选维度，保存后生效。</div>
        <button
          type="button"
          class="h-[30px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#2e2f32] hover:bg-[#f5f7fb] disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!dimensionDrafts.some((item) => item.selected)"
          @click="emit('removeSelectedDimensionDrafts')"
        >
          删除选中
        </button>
      </div>
      <div class="border border-[#e3e5eb] rounded-lg overflow-hidden">
        <div
          class="grid grid-cols-[44px_58px_1fr_160px_130px] h-[38px] items-center bg-[#f7f8fa] text-[12px] font-medium text-[#4b5563] border-b border-[#e3e5eb]"
        >
          <div class="text-center">选择</div>
          <div>顺序</div>
          <div>维度</div>
          <div>展示名称</div>
          <div>时间粒度</div>
        </div>
        <div
          v-if="dimensionDrafts.length === 0"
          class="py-8 text-center text-[13px] text-[#9ca3af]"
        >
          暂无维度
        </div>
        <div
          v-for="(item, index) in dimensionDrafts"
          :key="item.id"
          class="grid grid-cols-[44px_58px_1fr_160px_130px] min-h-[44px] items-center text-[13px] text-[#2e2f32] border-b border-[#eef0f4] last:border-b-0"
        >
          <div class="text-center">
            <input v-model="item.selected" type="checkbox" class="accent-[#165DFF]" />
          </div>
          <div class="flex items-center gap-1">
            <button
              type="button"
              class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30"
              :disabled="index === 0"
              @click="emit('moveDimensionDraft', index, -1)"
            >
              <i class="fa-solid fa-arrow-up text-[11px]"></i></button
            ><button
              type="button"
              class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30"
              :disabled="index === dimensionDrafts.length - 1"
              @click="emit('moveDimensionDraft', index, 1)"
            >
              <i class="fa-solid fa-arrow-down text-[11px]"></i>
            </button>
          </div>
          <div class="pr-3 overflow-hidden">
            <div class="truncate" :title="getDimensionDisplayName(item)">
              {{ getDimensionDisplayName(item) }}
            </div>
            <div
              class="text-[11px] text-[#9ca3af] truncate"
              :title="`${item.table}.${item.column}`"
            >
              {{ item.table }}.{{ item.column }}
            </div>
          </div>
          <div class="pr-3">
            <input
              v-model="item.displayName"
              class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] outline-none focus:border-[#165DFF]"
              :placeholder="item.column"
            />
          </div>
          <div class="pr-3">
            <select
              v-if="isDimensionDateField(item)"
              v-model="item.transform"
              class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] bg-white outline-none focus:border-[#165DFF]"
            >
              <option v-for="opt in transformOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <div
              v-else
              class="h-[30px] inline-flex items-center px-2 rounded border border-[#eef0f4] bg-[#f8f9fb] text-[12px] text-[#9ca3af]"
            >
              原始值
            </div>
          </div>
        </div>
      </div>
    </div>
    <template #footer
      ><div class="flex justify-end gap-2">
        <el-button @click="showDimensionManager = false">取消</el-button
        ><el-button type="primary" @click="emit('saveDimensionManager')">保存</el-button>
      </div></template
    >
  </el-dialog>

  <el-dialog
    v-model="showAggregationManager"
    width="780px"
    :close-on-click-modal="false"
    title="指标管理"
  >
    <div class="space-y-3">
      <div class="flex items-center justify-between">
        <div class="text-[12px] text-[#6b7280]">批量管理当前查询已选指标，保存后生效。</div>
        <button
          type="button"
          class="h-[30px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#2e2f32] hover:bg-[#f5f7fb] disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!aggregationDrafts.some((item) => item.selected)"
          @click="emit('removeSelectedAggregationDrafts')"
        >
          删除选中
        </button>
      </div>
      <div class="border border-[#e3e5eb] rounded-lg overflow-hidden">
        <div
          class="grid grid-cols-[44px_58px_1fr_160px_140px] h-[38px] items-center bg-[#f7f8fa] text-[12px] font-medium text-[#4b5563] border-b border-[#e3e5eb]"
        >
          <div class="text-center">选择</div>
          <div>顺序</div>
          <div>指标</div>
          <div>展示名称</div>
          <div>聚合函数</div>
        </div>
        <div
          v-if="aggregationDrafts.length === 0"
          class="py-8 text-center text-[13px] text-[#9ca3af]"
        >
          暂无指标
        </div>
        <div
          v-for="(item, index) in aggregationDrafts"
          :key="item.id"
          class="grid grid-cols-[44px_58px_1fr_160px_140px] min-h-[44px] items-center text-[13px] text-[#2e2f32] border-b border-[#eef0f4] last:border-b-0"
        >
          <div class="text-center">
            <input v-model="item.selected" type="checkbox" class="accent-[#165DFF]" />
          </div>
          <div class="flex items-center gap-1">
            <button
              type="button"
              class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30"
              :disabled="index === 0"
              @click="emit('moveAggregationDraft', index, -1)"
            >
              <i class="fa-solid fa-arrow-up text-[11px]"></i></button
            ><button
              type="button"
              class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30"
              :disabled="index === aggregationDrafts.length - 1"
              @click="emit('moveAggregationDraft', index, 1)"
            >
              <i class="fa-solid fa-arrow-down text-[11px]"></i>
            </button>
          </div>
          <div class="pr-3 overflow-hidden">
            <div class="truncate" :title="getMetricDisplayName(item)">
              {{ getMetricDisplayName(item) }}
            </div>
            <div
              class="text-[11px] text-[#9ca3af] truncate"
              :title="`${item.table}.${item.column}`"
            >
              {{ item.table }}.{{ item.column }}
            </div>
          </div>
          <div class="pr-3">
            <input
              v-model="item.displayName"
              class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] outline-none focus:border-[#165DFF]"
              :placeholder="item.column"
            />
          </div>
          <div class="pr-3">
            <select
              v-model="item.func"
              class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] bg-white outline-none focus:border-[#165DFF]"
            >
              <option v-for="opt in aggFunctions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </div>
        </div>
      </div>
    </div>
    <template #footer
      ><div class="flex justify-end gap-2">
        <el-button @click="showAggregationManager = false">取消</el-button
        ><el-button type="primary" @click="emit('saveAggregationManager')">保存</el-button>
      </div></template
    >
  </el-dialog>
</template>
