<script setup lang="ts">
/**
 * 后台管理 · 运行配置面板。
 * 列表即全部可动态修改变量（代码注册表白名单，key 与 .env 同名）——
 * 每行「覆盖/编辑」改值、「重置」删覆盖行回退 env/默认；无自由新增入口。
 * 密钥与启动级变量不在注册表内，不进本面板。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteAdminSetting, getAdminSettings, resetAllAdminSettings, upsertAdminSetting } from '../api'
import type { ChatDataSettingItem } from '../types'
import AdminPanelToolbar from './AdminPanelToolbar.vue'

const loading = ref(false)
const items = ref<ChatDataSettingItem[]>([])
const keyword = ref('')
const hasLoaded = ref(false)

// 客户端分页：列表本就是白名单全量（撑死几十行），无需后端分页
const page = ref(1)
const pageSize = ref(20)
const pagedItems = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return items.value.slice(start, start + pageSize.value)
})

const dialogVisible = ref(false)
const saving = ref(false)
const editingKey = ref('')
const formValue = ref('')

// 值控件按注册类型渲染：bool 用下拉，其余文本框（服务端按注册 spec 校验）
const formKeyType = computed(() => {
  const item = items.value.find(i => i.setting_key === editingKey.value)
  return item?.value_type ?? 'string'
})
const editingRow = computed(() => items.value.find(i => i.setting_key === editingKey.value))

const SOURCE_BADGES: Record<ChatDataSettingItem['source'], { label: string; class: string }> = {
  table: { label: '表覆盖', class: 'bg-blue-50 text-blue-600' },
  env: { label: 'env', class: 'bg-amber-50 text-amber-700' },
  default: { label: '默认', class: 'bg-slate-100 text-slate-500' }
}

const loadSettings = async () => {
  loading.value = true
  try {
    const res = await getAdminSettings({ keyword: keyword.value.trim() || undefined })
    if (res.status === 'success') {
      items.value = res.data.items || []
      hasLoaded.value = true
      // 数据变化后若当前页越界则回退，避免空页
      const maxPage = Math.max(1, Math.ceil(items.value.length / pageSize.value))
      if (page.value > maxPage) page.value = maxPage
    } else {
      ElMessage.error(res.msg || '获取配置变量列表失败')
    }
  } catch (err) {
    console.error('获取配置变量列表失败:', err)
    ElMessage.error('获取配置变量列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadSettings()
}

const searchSettings = () => {
  void loadSettings()
}

// 「覆盖」= 对 env/默认来源的行新建覆盖；「编辑」= 改已有覆盖行的值
const openEdit = (row: ChatDataSettingItem) => {
  editingKey.value = row.setting_key
  formValue.value = row.setting_value ?? String(row.effective_value ?? '')
  dialogVisible.value = true
}

const saveSetting = async () => {
  if (!formValue.value.trim()) {
    ElMessage.warning('请填写变量值')
    return
  }
  saving.value = true
  try {
    const res = await upsertAdminSetting(editingKey.value, {
      setting_value: formValue.value
    })
    if (res.status === 'success') {
      const action = res.data?.action
      ElMessage.success(action === 'restored' ? '已恢复该变量覆盖' : action === 'created' ? '已覆盖该变量' : '已更新变量覆盖')
      dialogVisible.value = false
      await loadSettings()
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    console.error('保存配置变量失败:', err)
    ElMessage.error('保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

// 「重置」= 软删覆盖行，变量回退 env / 代码默认
const resetSetting = async (row: ChatDataSettingItem) => {
  try {
    await ElMessageBox.confirm(
      `重置后「${row.setting_key}」将回退 env / 代码默认值（可随时重新覆盖）。确定重置？`,
      '重置变量覆盖',
      { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminSetting(row.setting_key)
    if (res.status === 'success') {
      ElMessage.success('已重置，该变量回退 env/默认值')
      await loadSettings()
    } else {
      ElMessage.error(res.msg || '重置失败')
    }
  } catch (err) {
    console.error('重置配置变量失败:', err)
    ElMessage.error('重置失败，请稍后重试')
  }
}

const resetAll = async () => {
  const overridden = items.value.filter(i => i.source === 'table')
  if (overridden.length === 0) {
    ElMessage.info('当前没有任何变量被表覆盖，无需重置')
    return
  }
  try {
    await ElMessageBox.confirm(
      `当前有 ${overridden.length} 个变量被表覆盖（${overridden.map(i => i.setting_key).join('、')}）。全部重置后这些变量将回退 env / 代码默认值。确定全部重置？`,
      '全部重置',
      { type: 'warning', confirmButtonText: '全部重置', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await resetAllAdminSettings()
    if (res.status === 'success') {
      ElMessage.success(`已重置 ${res.data?.reset_count ?? 0} 个变量覆盖`)
      await loadSettings()
    } else {
      ElMessage.error(res.msg || '全部重置失败')
    }
  } catch (err) {
    console.error('全部重置失败:', err)
    ElMessage.error('全部重置失败，请稍后重试')
  }
}

const formatEffectiveValue = (row: ChatDataSettingItem) => {
  if (row.value_invalid) return `${String(row.effective_value)}（表值非法，已回退）`
  const value = row.effective_value
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 顶区：说明条 + 工具栏 -->
    <AdminPanelToolbar>
      <el-input
        v-model="keyword"
        size="small"
        placeholder="搜索变量名或说明"
        clearable
        class="!w-56"
        @keyup.enter="searchSettings"
        @clear="searchSettings"
      />
      <el-button size="small" @click="searchSettings">搜索</el-button>
      <el-button size="small" :loading="loading" @click="loadSettings">
        <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
      </el-button>
      <template #actions>
        <el-button size="small" type="danger" plain round @click="resetAll">
          <i class="fa-solid fa-arrow-rotate-left" style="margin-right: 4px; font-size: 10px;"></i>
          全部重置
        </el-button>
      </template>
    </AdminPanelToolbar>

    <!-- 变量列表（按分类分组排序 + 客户端分页；分类列不合并单元格） -->
    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <el-table
        :data="pagedItems"
        v-loading="loading"
        size="small"
        height="100%"
        style="width: 100%"
        empty-text="暂无配置变量"
      >
        <el-table-column label="分类" width="100" align="center">
          <template #default="{ row }">
            <span class="text-xs font-medium text-slate-600">{{ row.category_label }}</span>
          </template>
        </el-table-column>
        <el-table-column label="变量名" min-width="300" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="font-mono text-xs text-slate-800">{{ row.setting_key }}</span>
          </template>
        </el-table-column>
        <el-table-column label="说明" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.setting_desc || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="生效值" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="font-mono text-xs" :class="row.value_invalid ? 'text-red-500' : 'text-slate-700'">
              {{ formatEffectiveValue(row) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="90" align="center">
          <template #default="{ row }">
            <span :class="['inline-flex h-5 items-center rounded-full px-2 text-[11px]', SOURCE_BADGES[row.source as keyof typeof SOURCE_BADGES]?.class]">
              {{ SOURCE_BADGES[row.source as keyof typeof SOURCE_BADGES]?.label }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="70" align="center">
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.value_type }}</span>
          </template>
        </el-table-column>
        <el-table-column label="生效时机" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.effect_label }}</span>
          </template>
        </el-table-column>
        <el-table-column label="修改时间" width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.update_time" class="text-xs text-slate-500">{{ row.update_time }}</span>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="修改人员" width="100" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.update_time" class="text-xs text-slate-500">{{ row.update_by_name || '-' }}</span>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right" class-name="setting-op-col">
          <template #default="{ row }">
            <el-button link size="small" @click="openEdit(row)">
              <i class="fa-solid fa-pen text-[11px]"></i>
              {{ row.source === 'table' ? '编辑' : '覆盖' }}
            </el-button>
            <el-button v-if="row.source === 'table'" link size="small" type="danger" @click="resetSetting(row)">
              <i class="fa-solid fa-arrow-rotate-left text-[11px]"></i>
              重置
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div v-if="items.length > 0" class="flex shrink-0 justify-end pt-2">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="items.length"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          small
          background
        />
      </div>
    </div>

    <!-- 覆盖/编辑弹窗（key 来自列表行，无自由输入） -->
    <el-dialog v-model="dialogVisible" width="min(520px, 94vw)" :append-to-body="true">
      <template #header>
        <span class="text-base font-medium text-slate-800">{{ editingRow?.source === 'table' ? '编辑变量覆盖' : '覆盖变量' }}</span>
      </template>
      <div class="flex flex-col gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">变量名</div>
          <div class="font-mono text-xs text-slate-800">{{ editingKey }}</div>
          <div class="mt-1 text-[11px] text-slate-400">{{ editingRow?.setting_desc }}</div>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">变量值</div>
          <el-select v-if="formKeyType === 'bool'" v-model="formValue" size="small" class="!w-40">
            <el-option label="true" value="true" />
            <el-option label="false" value="false" />
          </el-select>
          <el-input v-else v-model="formValue" size="small" placeholder="变量值（字符串存储，按类型解析）" />
          <div class="mt-1 text-[11px] text-slate-400">当前生效值：{{ editingRow ? formatEffectiveValue(editingRow) : '-' }}</div>
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" size="small" :loading="saving" @click="saveSetting">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 操作列「编辑/覆盖 + 重置」两个按钮必须同行，不许折行（列宽 140 + 禁换行双保险） */
:deep(.setting-op-col .cell) {
  white-space: nowrap;
}
</style>
