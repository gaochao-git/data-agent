<script setup lang="ts">
/**
 * 对象类型 · 对象实例工作台。
 * 主从布局：左侧分页列表/搜索，右侧对象详情；关联对象在当前工作台下钻，不再弹窗跳转。
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  getAdminOntologyObjectType,
  getAdminOntologyRelationTypes,
  listAdminOntologyObjects,
  readAdminOntologyObject
} from '../../api'
import type { OntologyObjectInstance, OntologyObjectListItem, OntologyObjectTypeItem } from '../../types'

const props = defineProps({
  objectType: { type: String, default: '' },
  objectLabel: { type: String, default: '' }
})

const loading = ref(false)
const error = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = 20
const objects = ref<OntologyObjectListItem[]>([])
const hasMore = ref(false)
const titleProperty = ref('')

const meta = ref<OntologyObjectTypeItem | null>(null)
const relationLabels = ref<Record<string, string>>({})
const selectedKey = ref('')
const listSelectedKey = ref('')
const detail = ref<OntologyObjectInstance | null>(null)
const detailLoading = ref(false)
const linksLoading = ref(false)
const detailError = ref('')
const detailCache = new Map<string, OntologyObjectInstance>()
const history = ref<Array<{ objectType: string; objectKey: string; label: string }>>([])

const propertyLabels = computed<Record<string, string>>(() => {
  const labels: Record<string, string> = {}
  for (const prop of meta.value?.properties || []) labels[prop.property_name] = prop.display_name || prop.property_name
  return labels
})

const detailPropertyRows = computed(() => {
  const data = detail.value
  if (!data) return []
  return Object.entries(data.properties || {}).map(([name, value]) => ({
    name,
    label: propertyLabels.value[name] || '',
    value
  }))
})

const detailTitle = computed(() => {
  const data = detail.value
  if (!data) return ''
  const titleKey = meta.value?.properties?.find(item => item.is_title)?.property_name
  if (!titleKey) return ''
  const value = data.properties?.[titleKey]
  return value == null ? '' : String(value)
})

const linkedGroups = computed(() => {
  const data = detail.value
  if (!data) return []
  return Object.entries(data.links || {}).map(([relationName, rawEntries]) => {
    const entries = Array.isArray(rawEntries)
      ? rawEntries.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      : []
    return {
      relationName,
      label: relationLabels.value[relationName] || relationName,
      entries
    }
  })
})

const loadMeta = async (objectType: string) => {
  if (!objectType) return
  const [metaRes, fromRes, toRes] = await Promise.all([
    getAdminOntologyObjectType(objectType),
    getAdminOntologyRelationTypes({ from_object_type: objectType }),
    getAdminOntologyRelationTypes({ to_object_type: objectType })
  ])
  if (metaRes.status === 'success') meta.value = metaRes.data
  const labels: Record<string, string> = {}
  if (fromRes.status === 'success') {
    for (const relation of fromRes.data.relation_types || []) {
      labels[relation.relation_type_name] = relation.from_display_name || relation.relation_type_name
    }
  }
  if (toRes.status === 'success') {
    for (const relation of toRes.data.relation_types || []) {
      labels[relation.relation_type_name] = relation.to_display_name || labels[relation.relation_type_name] || relation.relation_type_name
    }
  }
  relationLabels.value = labels
}

const loadList = async (resetPage = false) => {
  if (!props.objectType) return
  if (resetPage) page.value = 1
  loading.value = true
  error.value = ''
  try {
    const res = await listAdminOntologyObjects(props.objectType, {
      keyword: keyword.value.trim() || undefined,
      limit: pageSize,
      offset: (page.value - 1) * pageSize
    })
    if (res.status === 'success') {
      objects.value = res.data.objects || []
      hasMore.value = Boolean(res.data.has_more)
      titleProperty.value = res.data.title_property || ''
    } else {
      error.value = res.msg || '加载对象列表失败'
    }
  } catch (err) {
    console.error('加载对象列表失败:', err)
    error.value = '加载对象列表失败，请检查映射和数据源权限'
  } finally {
    loading.value = false
  }
}

const loadDetail = async (rawKey: string, objectType = props.objectType, seed?: OntologyObjectListItem) => {
  const separator = rawKey.indexOf(':')
  const type = separator > 0 ? rawKey.slice(0, separator) : objectType
  const key = separator > 0 ? rawKey.slice(separator + 1) : rawKey
  selectedKey.value = rawKey
  detailError.value = ''
  const cached = detailCache.get(rawKey)
  if (cached) {
    detail.value = cached
    detailLoading.value = false
    linksLoading.value = false
    return
  }
  if (seed) {
    // 列表行已经带回属性：先渲染属性，再在后台补 links，避免点击后整块等待。
    detail.value = {
      object_type: type,
      object_key: rawKey,
      properties: seed.properties || {},
      links: {}
    }
    detailLoading.value = false
    linksLoading.value = true
  } else {
    detail.value = null
    detailLoading.value = true
    linksLoading.value = false
  }
  try {
    const res = await readAdminOntologyObject(type, key)
    if (res.status === 'success') {
      detail.value = res.data
      detailCache.set(rawKey, res.data)
      if (type !== props.objectType) await loadMeta(type)
    } else {
      detailError.value = res.msg || '读取对象失败'
    }
  } catch (err) {
    console.error('读取对象失败:', err)
    detailError.value = '读取对象失败，请检查映射和数据源权限'
  } finally {
    detailLoading.value = false
    linksLoading.value = false
  }
}

const selectRow = (row: OntologyObjectListItem) => {
  history.value = []
  listSelectedKey.value = row.object_key
  void loadDetail(row.object_key, props.objectType, row)
}

const openLinked = (rawKey: unknown) => {
  const key = String(rawKey || '')
  if (!key || !detail.value) return
  history.value.push({ objectType: props.objectType, objectKey: detail.value.object_key, label: detailTitle.value || detail.value.object_key })
  void loadDetail(key)
}

const backToObject = async () => {
  const previous = history.value.pop()
  if (!previous) return
  await loadDetail(previous.objectKey, previous.objectType)
  if (previous.objectType !== props.objectType) await loadMeta(previous.objectType)
}

const resetToList = () => {
  selectedKey.value = ''
  listSelectedKey.value = ''
  detail.value = null
  detailError.value = ''
  linksLoading.value = false
  history.value = []
}

const rowClassName = ({ row }: { row: OntologyObjectListItem }) =>
  row.object_key === listSelectedKey.value ? 'object-instance-row--active' : ''

const titleValue = (row: OntologyObjectListItem) => {
  if (!titleProperty.value) return ''
  const value = row.properties?.[titleProperty.value]
  return value == null ? '' : String(value)
}

const previewValueText = (value: unknown) => {
  if (value == null) return '-'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

const initialize = () => {
  if (!props.objectType) return
  keyword.value = ''
  page.value = 1
  resetToList()
  void loadMeta(props.objectType)
  void loadList()
}

watch(() => props.objectType, initialize)
onMounted(initialize)
</script>

<template>
  <div class="flex h-[640px] min-h-0 gap-4">
    <section class="flex w-[360px] shrink-0 flex-col border-r border-slate-100 pr-3">
      <div class="mb-2 flex items-center gap-2">
        <el-input
          v-model="keyword"
          size="small"
          clearable
          placeholder="搜索标题键或主键"
          @keyup.enter="loadList(true)"
          @clear="loadList(true)"
        />
        <el-button size="small" type="primary" @click="loadList(true)">搜索</el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-2" />
      <el-table
        v-loading="loading"
        :data="objects"
        size="small"
        border
        height="540"
        empty-text="暂无对象数据"
        :row-class-name="rowClassName"
        @row-click="selectRow"
      >
        <el-table-column v-if="titleProperty" label="标题" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ titleValue(row) || '-' }}</template>
        </el-table-column>
        <el-table-column prop="object_key" label="对象主键" min-width="170" show-overflow-tooltip />
      </el-table>
      <div class="mt-2 flex items-center justify-end gap-2">
        <span class="text-[11px] text-slate-400">第 {{ page }} 页</span>
        <el-button size="small" :disabled="page <= 1 || loading" @click="page -= 1; loadList()">上一页</el-button>
        <el-button size="small" :disabled="!hasMore || loading" @click="page += 1; loadList()">下一页</el-button>
      </div>
    </section>

    <section class="min-w-0 flex-1 overflow-y-auto pl-1">
      <div v-if="!detail && !detailLoading" class="flex h-full items-center justify-center text-center text-xs text-slate-400">
        <div>
          <i class="fa-solid fa-table-list mb-2 block text-2xl text-slate-200"></i>
          从左侧列表选择一个对象
        </div>
      </div>
      <div v-else-if="detailLoading" class="flex h-full items-center justify-center text-xs text-slate-400">加载对象详情中…</div>
      <el-alert v-else-if="detailError" :title="detailError" type="error" :closable="false" show-icon />
      <template v-else-if="detail">
        <div class="mb-3 flex items-center gap-2">
          <el-button v-if="history.length > 0" link size="small" @click="backToObject">返回</el-button>
          <el-button link size="small" @click="resetToList">返回列表</el-button>
          <span class="font-mono text-xs text-slate-700">{{ detail.object_key }}</span>
          <span v-if="detailTitle" class="text-xs text-slate-500">{{ detailTitle }}</span>
        </div>

        <div class="mb-1 text-xs font-medium text-slate-600">属性</div>
        <el-table :data="detailPropertyRows" size="small" border empty-text="暂无属性">
          <el-table-column label="属性" min-width="150">
            <template #default="{ row }">
              <span class="font-mono text-xs text-slate-700">{{ row.name }}</span>
              <span v-if="row.label" class="ml-1.5 text-xs text-slate-400">{{ row.label }}</span>
            </template>
          </el-table-column>
          <el-table-column label="值" min-width="220">
            <template #default="{ row }">{{ previewValueText(row.value) }}</template>
          </el-table-column>
        </el-table>

        <div v-if="linksLoading" class="mt-3 text-xs text-slate-400">加载关联对象中…</div>
        <template v-else-if="linkedGroups.length > 0">
          <div class="mb-1 mt-3 text-xs font-medium text-slate-600">关联对象</div>
          <div v-for="group in linkedGroups" :key="group.relationName" class="mb-2 rounded border border-slate-200 bg-white px-3 py-2">
            <div class="flex items-center gap-2">
              <span class="text-xs text-slate-700">{{ group.label }}</span>
              <el-tag size="small" type="info" effect="plain">{{ group.entries.length }}</el-tag>
            </div>
            <div class="mt-1 flex flex-col gap-0.5">
              <button
                v-for="(item, index) in group.entries"
                :key="index"
                type="button"
                class="w-full border-0 bg-transparent p-0 text-left font-mono text-[11px] text-slate-500 hover:text-blue-600"
                @click="openLinked(item.related_object_key)"
              >
                {{ item.related_object_key || '-' }}
              </button>
            </div>
          </div>
        </template>
      </template>
    </section>
  </div>
</template>


<style scoped>
:deep(.object-instance-row--active > td.el-table__cell) {
  background-color: #eff6ff;
}
:deep(.object-instance-row--active:hover > td.el-table__cell) {
  background-color: #dbeafe;
}
</style>
