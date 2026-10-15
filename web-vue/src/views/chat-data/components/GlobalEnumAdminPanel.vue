<script setup lang="ts">
/**
 * 后台管理 · 全局枚举管理面板。
 * 全局枚举是全系统枚举值域唯一权威（跨数据源跨环境共享）；唯一键为
 * (业务域, 枚举名)，不同业务域同名枚举互相隔离。数据字典列通过 enum_ref
 * 引用（二选一：引用后不能本地再维护枚举值）。修改后随自动物化下一轮生效。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deleteAdminMetaEnum,
  generateAdminMetaEnumItems,
  getAdminMetaEnumDomains,
  getAdminMetaEnumRefs,
  getAdminMetaEnums,
  upsertAdminMetaEnum,
} from '../api'
import type { MetaGlobalEnum, MetaGlobalEnumRef, MetaGlobalEnumValueItem } from '../types'

const loading = ref(false)
const items = ref<MetaGlobalEnum[]>([])
const keyword = ref('')
// 空串表示仅看通用域；undefined 表示全部域；具体值表示指定业务域
const filterDomain = ref<string | undefined>(undefined)
const knownDomains = ref<string[]>([])
const hasLoaded = ref(false)

// 客户端分页：枚举量为公司级配置（量级有限），无需后端分页
const page = ref(1)
const pageSize = ref(20)
const pagedItems = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return items.value.slice(start, start + pageSize.value)
})

// 编辑弹窗：creating=true 时允许输入枚举名，否则只读
const dialogVisible = ref(false)
const saving = ref(false)
const creating = ref(false)
const editingDomain = ref('')
const editingName = ref('')
const formDomain = ref('')
const formName = ref('')
const formDesc = ref('')
const formItems = ref<MetaGlobalEnumValueItem[]>([])

// AI 填充：从粘贴文本提取枚举值候选（复用字段字典生成链路），不直接保存
const aiGenerateText = ref('')
const aiGenerating = ref(false)

// 引用方弹窗
const refsDialogVisible = ref(false)
const refsLoading = ref(false)
const refsEnumName = ref('')
const refsEnumDomain = ref('')
const refRows = ref<MetaGlobalEnumRef[]>([])

const ENUM_NAME_PATTERN = /^[A-Za-z0-9_]{1,128}$/

const displayDomain = (domain: string) => (domain ? domain : '通用域')

const previewValues = (row: MetaGlobalEnum) => {
  const parts = (row.enum_items || []).slice(0, 4).map(item => (item.label ? `${item.value}(${item.label})` : item.value))
  const more = row.enum_items.length > 4 ? ` 等${row.enum_items.length}项` : ''
  return parts.join('、') + more
}

const loadEnums = async () => {
  loading.value = true
  try {
    const res = await getAdminMetaEnums({
      keyword: keyword.value.trim() || undefined,
      business_domain: filterDomain.value
    })
    if (res.status === 'success') {
      items.value = res.data || []
      hasLoaded.value = true
      const maxPage = Math.max(1, Math.ceil(items.value.length / pageSize.value))
      if (page.value > maxPage) page.value = maxPage
    } else {
      ElMessage.error(res.msg || '获取全局枚举列表失败')
    }
  } catch (err) {
    // axios 拦截器已对业务错误（ElNotification）与网络错误（ElMessage）弹出具体原因，此处不再重复提示
    console.error('获取全局枚举列表失败:', err)
  } finally {
    loading.value = false
  }
}

const loadDomains = async () => {
  try {
    const res = await getAdminMetaEnumDomains()
    if (res.status === 'success') {
      knownDomains.value = res.data?.domains || []
    }
  } catch (err) {
    console.error('获取业务域清单失败:', err)
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) {
    await Promise.all([loadEnums(), loadDomains()])
  }
}

const searchEnums = () => {
  void loadEnums()
}

const onFilterDomainChange = () => {
  page.value = 1
  void loadEnums()
}

const openCreate = () => {
  creating.value = true
  editingDomain.value = ''
  editingName.value = ''
  // 默认带上当前筛选域，方便连续维护同一域的枚举
  formDomain.value = filterDomain.value || ''
  formName.value = ''
  formDesc.value = ''
  formItems.value = [{ value: '', label: '' }]
  aiGenerateText.value = ''
  void loadDomains()
  dialogVisible.value = true
}

const openEdit = (row: MetaGlobalEnum) => {
  creating.value = false
  editingDomain.value = row.business_domain || ''
  editingName.value = row.enum_name
  formDomain.value = row.business_domain || ''
  formName.value = row.enum_name
  formDesc.value = row.enum_desc || ''
  formItems.value = (row.enum_items || []).map(item => ({ ...item }))
  aiGenerateText.value = ''
  void loadDomains()
  dialogVisible.value = true
}

const addFormItem = () => {
  formItems.value.push({ value: '', label: '' })
}

const removeFormItem = (index: number) => {
  formItems.value.splice(index, 1)
}

// AI 填充：value 已存在则只补空 label，不存在则新增（与列字典 AI 生成合并口径一致）
const aiFillItems = async () => {
  const text = aiGenerateText.value.trim()
  if (!text) {
    ElMessage.warning('请输入用于提取枚举值的文本')
    return
  }
  aiGenerating.value = true
  try {
    const res = await generateAdminMetaEnumItems(text)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '生成枚举值失败')
      return
    }
    const candidates = res.data?.enum_items || []
    if (candidates.length === 0) {
      ElMessage.warning('未识别到明确的枚举值')
      return
    }
    const nextRows = formItems.value.map(item => ({ value: item.value.trim(), label: (item.label || '').trim() }))
    const indexMap = new Map<string, number>()
    nextRows.forEach((row, index) => {
      if (row.value && !indexMap.has(row.value)) indexMap.set(row.value, index)
    })
    let addedCount = 0
    let filledCount = 0
    candidates.forEach(candidate => {
      const value = String(candidate.value || '').trim()
      const label = String(candidate.label || '').trim()
      if (!value) return
      const existingIndex = indexMap.get(value)
      if (existingIndex !== undefined) {
        const existingRow = nextRows[existingIndex]
        if (existingRow && !existingRow.label && label) {
          existingRow.label = label
          filledCount += 1
        }
        return
      }
      indexMap.set(value, nextRows.length)
      nextRows.push({ value, label })
      addedCount += 1
    })
    formItems.value = nextRows.length > 0 ? nextRows : [{ value: '', label: '' }]
    ElMessage.success(`已生成候选，新增枚举 ${addedCount} 个，补充含义 ${filledCount} 个`)
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('AI 生成全局枚举值失败:', err)
  } finally {
    aiGenerating.value = false
  }
}

const saveEnum = async () => {
  const domain = (creating.value ? formDomain.value : editingDomain.value).trim()
  const name = (creating.value ? formName.value : editingName.value).trim()
  if (!name) {
    ElMessage.warning('请填写枚举名')
    return
  }
  if (!ENUM_NAME_PATTERN.test(name)) {
    ElMessage.warning('枚举名仅允许字母、数字、下划线，长度 1-128')
    return
  }
  const cleanedItems = formItems.value
    .map(item => ({ value: item.value.trim(), label: (item.label || '').trim() }))
    .filter(item => item.value)
  if (cleanedItems.length === 0) {
    ElMessage.warning('请至少填写一个枚举值')
    return
  }
  const seen = new Set<string>()
  for (const item of cleanedItems) {
    if (seen.has(item.value)) {
      ElMessage.warning(`枚举值重复: ${item.value}`)
      return
    }
    seen.add(item.value)
  }
  saving.value = true
  try {
    const res = await upsertAdminMetaEnum(name, {
      business_domain: domain,
      enum_desc: formDesc.value.trim(),
      enum_items: cleanedItems,
      // 编辑态允许覆盖既有枚举内容；新建态不传，后端遇同名活跃枚举直接拒绝
      allow_overwrite: !creating.value
    })
    if (res.status === 'success') {
      ElMessage.success('保存成功，引用该枚举的数据字典将随自动物化生效')
      dialogVisible.value = false
      await Promise.all([loadEnums(), loadDomains()])
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('保存全局枚举失败:', err)
  } finally {
    saving.value = false
  }
}

// 迟到的旧枚举引用方响应不得覆盖新打开弹窗的内容（快速连续查看不同枚举时）
let refsLoadSeq = 0

const openRefs = async (row: MetaGlobalEnum) => {
  const seq = ++refsLoadSeq
  refsEnumDomain.value = row.business_domain || ''
  refsEnumName.value = row.enum_name
  refRows.value = []
  refsDialogVisible.value = true
  refsLoading.value = true
  try {
    const res = await getAdminMetaEnumRefs(row.enum_name, row.business_domain || undefined)
    if (seq !== refsLoadSeq) return
    if (res.status === 'success') {
      refRows.value = res.data?.refs || []
    } else {
      ElMessage.error(res.msg || '查询引用方失败')
    }
  } catch (err) {
    if (seq !== refsLoadSeq) return
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('查询全局枚举引用方失败:', err)
  } finally {
    if (seq === refsLoadSeq) {
      refsLoading.value = false
    }
  }
}

const removeEnum = async (row: MetaGlobalEnum) => {
  try {
    await ElMessageBox.confirm(
      `删除「${displayDomain(row.business_domain)} / ${row.enum_name}」后不可恢复；仍被数据字典引用时删除会被拒绝。确定删除？`,
      '删除全局枚举',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminMetaEnum(row.enum_name, row.business_domain || undefined)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      await Promise.all([loadEnums(), loadDomains()])
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如「仍被引用（…），请先解除引用」），不再重复提示
    console.error('删除全局枚举失败:', err)
  }
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 说明条 -->
    <div class="shrink-0 border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
      全局枚举供所有数据源共享，数据字典列可引用为枚举模板；不同业务域之间互相隔离。
    </div>

    <!-- 工具栏 -->
    <div class="flex shrink-0 items-center gap-2 px-4 py-2">
      <el-select
        v-model="filterDomain"
        size="small"
        placeholder="全部业务域"
        clearable
        :empty-values="[null, undefined]"
        class="!w-44"
        @change="onFilterDomainChange"
      >
        <el-option label="通用域（全公司共用）" value="" />
        <el-option v-for="domain in knownDomains" :key="domain" :label="domain" :value="domain" />
      </el-select>
      <el-input
        v-model="keyword"
        size="small"
        placeholder="搜索枚举名或说明"
        clearable
        class="!w-56"
        @keyup.enter="searchEnums"
        @clear="searchEnums"
      />
      <el-button size="small" @click="searchEnums">搜索</el-button>
      <el-button size="small" :loading="loading" @click="loadEnums">
        <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
      </el-button>
      <div class="flex-1"></div>
      <el-button size="small" type="primary" round @click="openCreate">
        <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
        新建枚举
      </el-button>
    </div>

    <!-- 枚举列表 -->
    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <el-table
        :data="pagedItems"
        v-loading="loading"
        size="small"
        height="100%"
        style="width: 100%"
        empty-text="暂无全局枚举"
      >
        <el-table-column label="业务域" width="130" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.business_domain" size="small" type="info" effect="plain">{{ row.business_domain }}</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">通用域</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="枚举名" min-width="190" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="font-mono text-xs text-slate-800">{{ row.enum_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.enum_desc || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="枚举值" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ previewValues(row) || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="值数" width="65" align="center">
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ (row.enum_items || []).length }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="145" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.update_time" class="text-xs text-slate-500">{{ row.update_time }}</span>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="修改人员" width="95" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.update_by_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right" class-name="enum-op-col">
          <template #default="{ row }">
            <el-button link size="small" @click="openEdit(row)">
              <i class="fa-solid fa-pen text-[11px]"></i> 编辑
            </el-button>
            <el-button link size="small" @click="openRefs(row)">
              <span class="font-medium">{{ row.ref_count ?? 0 }}</span>&nbsp;引用方
            </el-button>
            <el-button link size="small" type="danger" @click="removeEnum(row)">
              <i class="fa-solid fa-trash text-[11px]"></i> 删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="items.length > 0" class="flex shrink-0 justify-end pt-2">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="items.length"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          size="small"
          background
        />
      </div>
    </div>

    <!-- 编辑弹窗 -->
    <el-dialog v-model="dialogVisible" width="min(660px, 94vw)" :append-to-body="true">
      <template #header>
        <span class="text-base font-medium text-slate-800">
          {{ creating ? '新建全局枚举' : `编辑全局枚举：${displayDomain(editingDomain)} / ${editingName}` }}
        </span>
      </template>
      <div class="flex flex-col gap-3">
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="mb-1 text-xs text-slate-500">业务域（同域内枚举名唯一）</div>
            <el-select
              v-model="formDomain"
              size="small"
              :disabled="!creating"
              filterable
              allow-create
              default-first-option
              :empty-values="[null, undefined]"
              placeholder="请选择业务域，或输入新建"
              class="!w-full"
            >
              <el-option label="通用域（全公司共用）" value="" />
              <el-option v-for="domain in knownDomains" :key="domain" :label="domain" :value="domain" />
            </el-select>
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">枚举名</div>
            <el-input
              v-model="formName"
              size="small"
              :disabled="!creating"
              placeholder="仅字母、数字、下划线，如 pay_status"
              class="!w-full font-mono"
            />
          </div>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">说明</div>
          <el-input v-model="formDesc" size="small" maxlength="512" placeholder="枚举业务含义说明（可选）" />
        </div>
        <div>
          <div class="rounded-xl border border-blue-100 bg-blue-50 p-3">
            <div class="mb-2 flex items-center justify-between gap-2">
              <div class="flex items-center gap-2 text-xs font-medium text-blue-700">
                <i class="fa-solid fa-wand-magic-sparkles text-[11px]"></i>
                <span>AI 填充枚举值</span>
              </div>
              <button
                type="button"
                :disabled="aiGenerating || !aiGenerateText.trim()"
                class="flex items-center gap-1.5 rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                @click="aiFillItems"
              >
                <i :class="['fa-solid', aiGenerating ? 'fa-spinner fa-spin' : 'fa-wand-magic-sparkles', 'text-[10px]']"></i>
                <span>{{ aiGenerating ? '生成中' : '生成候选' }}</span>
              </button>
            </div>
            <textarea
              v-model="aiGenerateText"
              maxlength="10000"
              rows="3"
              placeholder="粘贴枚举说明，例如：订单状态：0 待支付、1 已支付、2 已取消"
              class="w-full resize-none rounded border border-blue-100 bg-white px-2.5 py-2 text-xs leading-5 text-slate-700 outline-none focus:border-blue-500"
            ></textarea>
          </div>
        </div>
        <div>
          <div class="mb-1 flex items-center justify-between">
            <span class="text-xs text-slate-500">枚举值（value 为入库真实值，label 为用户可读含义）</span>
            <el-button link size="small" type="primary" @click="addFormItem">
              <i class="fa-solid fa-plus text-[11px]"></i> 添加
            </el-button>
          </div>
          <div class="flex max-h-64 flex-col gap-1.5 overflow-y-auto pr-1">
            <div v-for="(item, index) in formItems" :key="index" class="flex items-center gap-2">
              <el-input v-model="item.value" size="small" placeholder="值" class="!w-56 font-mono" />
              <el-input v-model="item.label" size="small" placeholder="含义（可选）" class="flex-1" />
              <el-button
                link
                size="small"
                type="danger"
                :disabled="formItems.length <= 1"
                @click="removeFormItem(index)"
              >
                <i class="fa-solid fa-trash text-[11px]"></i>
              </el-button>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" size="small" :loading="saving" @click="saveEnum">保存</el-button>
      </template>
    </el-dialog>

    <!-- 引用方弹窗 -->
    <el-dialog v-model="refsDialogVisible" width="min(640px, 94vw)" :append-to-body="true">
      <template #header>
        <span class="text-base font-medium text-slate-800">
          引用方：{{ displayDomain(refsEnumDomain) }} / {{ refsEnumName }}
        </span>
      </template>
      <el-table :data="refRows" v-loading="refsLoading" size="small" max-height="360" empty-text="暂无引用">
        <el-table-column label="数据源组" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.group_name || row.group_id }}</template>
        </el-table-column>
        <el-table-column label="表名" prop="table_name" min-width="160" show-overflow-tooltip />
        <el-table-column label="字段名" prop="column_name" min-width="160" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 操作列三个按钮必须同行，不许折行 */
:deep(.enum-op-col .cell) {
  white-space: nowrap;
}
</style>
