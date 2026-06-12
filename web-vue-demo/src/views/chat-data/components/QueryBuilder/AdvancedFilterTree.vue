<script setup lang="ts">
import { computed } from 'vue'

type FilterLogic = 'AND' | 'OR'

interface AdvancedFilterLeaf {
  kind: 'leaf'
  id: string
  filter: { id: string; [key: string]: unknown }
}

interface AdvancedFilterGroup {
  kind: 'group'
  id: string
  logic: FilterLogic
  children: Array<AdvancedFilterLeaf | AdvancedFilterGroup>
}

defineOptions({ name: 'AdvancedFilterTree' })

const props = defineProps<{
  group: AdvancedFilterGroup
  depth?: number
  describeLeaf: (leaf: any) => string
}>()

const emit = defineEmits<{
  toggleLogic: [groupId: string]
  addLeaf: [groupId: string]
  addGroup: [groupId: string]
  removeNode: [nodeId: string]
  editLeaf: [filterId: string]
}>()

const currentDepth = computed(() => props.depth || 0)
const groupIndent = computed(() => `${64 + currentDepth.value * 20}px`)
const groupPanelClass = computed(() => {
  if (currentDepth.value % 3 === 0) return 'bg-[#fcfdff] border-[#e8edf7]'
  if (currentDepth.value % 3 === 1) return 'bg-[#f8fbff] border-[#dfe8f7]'
  return 'bg-[#fdfaf6] border-[#eadfce]'
})

const getLeafText = (leaf: any) => String(props.describeLeaf(leaf) || '').trim()

const isLeafPlaceholder = (leaf: any) => {
  const text = getLeafText(leaf)
  return text === '' || text === '编辑筛选器' || text === '点击选择字段'
}
</script>

<template>
  <div class="relative" :class="currentDepth > 0 ? 'mt-3' : ''">
    <div class="relative" :style="{ paddingLeft: groupIndent }">
      <div class="absolute top-3 bottom-3 w-[14px] pointer-events-none" :style="{ left: `${22 + currentDepth * 20}px` }">
        <span class="absolute left-1/2 top-0 w-[10px] h-0 border-t border-[#cdb393]"></span>
        <span class="absolute left-1/2 bottom-0 w-[10px] h-0 border-t border-[#cdb393]"></span>
        <span class="absolute left-1/2 top-0 bottom-0 w-0 border-l border-[#cdb393]"></span>
      </div>
      <div class="absolute top-1/2 -translate-y-1/2" :style="{ left: `${2 + currentDepth * 20}px` }">
        <button
          type="button"
          class="inline-flex items-center justify-center rounded-full border border-[#e8d5c2] bg-[#f4dfca] text-[#2e2f32] font-semibold shadow-[0_1px_2px_rgba(0,0,0,0.08)] hover:border-[#d8b896]"
          :class="currentDepth > 0 ? 'h-[28px] px-2 text-[12px]' : 'h-[34px] px-2.5 text-[14px]'"
          @click="emit('toggleLogic', group.id)"
        >
          {{ group.logic }}
        </button>
      </div>

      <div class="rounded-[14px] border px-3 py-2" :class="groupPanelClass">
        <div class="space-y-2">
          <template v-for="child in group.children" :key="child.id">
            <div v-if="child.kind === 'leaf'" class="relative w-[65%] min-w-[320px] max-w-[980px]">
              <button
                type="button"
                class="h-[42px] w-full pl-4 pr-10 rounded-[18px] border text-left text-[13px] overflow-hidden text-ellipsis whitespace-nowrap transition-colors"
                :class="isLeafPlaceholder(child)
                  ? 'border-dashed border-[#9ca3af] text-[#c0c4cc] bg-white'
                  : 'border-[#d7e3ff] bg-[#eaf0ff] text-[#2e2f32] hover:bg-[#dde8ff]'"
                :title="getLeafText(child)"
                @click="emit('editLeaf', child.filter.id)"
              >
                {{ getLeafText(child) || '点击选择字段' }}
              </button>
              <button
                type="button"
                class="absolute right-2 top-1/2 -translate-y-1/2 w-[26px] h-[26px] rounded flex items-center justify-center text-[#7b808a] hover:bg-[#dfe7fb] hover:text-[#3f4550]"
                @click.stop="emit('removeNode', child.id)"
              >
                <i class="fa-regular fa-trash-can text-[18px]"></i>
              </button>
            </div>
            <div v-else class="rounded mt-1 flex items-center gap-2">
              <div class="flex-1 min-w-0">
                <AdvancedFilterTree
                  :group="child"
                  :depth="currentDepth + 1"
                  :describe-leaf="describeLeaf"
                  @toggle-logic="emit('toggleLogic', $event)"
                  @add-leaf="emit('addLeaf', $event)"
                  @add-group="emit('addGroup', $event)"
                  @remove-node="emit('removeNode', $event)"
                  @edit-leaf="emit('editLeaf', $event)"
                />
              </div>
              <button
                type="button"
                class="h-[30px] px-2.5 rounded-[8px] border border-[#ffd9d9] bg-[#fff4f4] inline-flex items-center gap-1 text-[12px] text-[#F53F3F] hover:text-[#CB272D] hover:bg-[#ffecec] shrink-0"
                @click="emit('removeNode', child.id)"
              >
                <i class="fa-regular fa-trash-can text-[12px]"></i>
                删除该组
              </button>
            </div>
          </template>
        </div>
        <div class="flex items-center gap-3 pt-1">
          <button
            type="button"
            class="h-[38px] px-4 rounded-[10px] border border-[#d9dde7] bg-white text-[13px] font-medium text-[#2d6eff] hover:bg-[#f4f8ff]"
            @click="emit('addLeaf', group.id)"
          >
            + 添加筛选项
          </button>
          <button
            type="button"
            class="h-[38px] px-4 rounded-[10px] border border-[#d9dde7] bg-white text-[13px] font-medium text-[#2d6eff] hover:bg-[#f4f8ff]"
            @click="emit('addGroup', group.id)"
          >
            + 添加筛选组
          </button>
        </div>
        <div v-if="group.children.length === 0" class="h-[32px] flex items-center px-1 text-[13px] text-[#9CA3AF]">
          点击“添加筛选项”开始配置
        </div>
      </div>
    </div>
  </div>
</template>
