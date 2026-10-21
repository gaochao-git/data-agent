<script setup lang="ts">
/**
 * 后台管理 · 插件面板。
 * 已安装插件列表 + zip 安装 + 启停 + 卸载；安装/启停对新会话生效（接口语义见 plugins/README.md）。
 */
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import { getAdminPlugins, installPlugin, setAdminPluginEnabled, uninstallPlugin } from '../api'
import type { PluginInfo, PluginMcpServerInfo } from '../types'
import AdminPanelToolbar from './AdminPanelToolbar.vue'

// 与后端 installer 的 zip 上限一致（50MB）
const PLUGIN_UPLOAD_MAX_BYTES = 50 * 1024 * 1024

const loading = ref(false)
const plugins = ref<PluginInfo[]>([])
const hasLoaded = ref(false)
const uploadRef = ref<UploadInstance | null>(null)
const installing = ref(false)

// MCP server 准入状态展示：未通过标红并给出原因，通过但有告警也提示
const mcpServerTagType = (server: PluginMcpServerInfo) =>
  server.admission && !server.admission.ok ? 'danger' : 'info'
const mcpServerLabel = (server: PluginMcpServerInfo) =>
  `${server.id}(${server.transport})${server.admission && !server.admission.ok ? ' · 未通过准入' : ''}`
// 每工具审批配置摘要：强制/免审/禁用分档计数，未配置的工具缺省"执行前审批"
const mcpApprovalSummary = (server: PluginMcpServerInfo) => {
  const entries = Object.entries(server.tool_approvals || {})
  if (!entries.length) return '\n审批配置：全部默认执行前审批'
  const counts = { force_ask: 0, allow: 0, deny: 0 } as Record<string, number>
  entries.forEach(([, value]) => {
    if (value in counts) counts[value] = (counts[value] ?? 0) + 1
  })
  const parts = [
    counts.force_ask ? `${counts.force_ask} 强制审批` : '',
    counts.allow ? `${counts.allow} 免审批` : '',
    counts.deny ? `${counts.deny} 禁用` : ''
  ].filter(Boolean)
  return parts.length ? `\n审批配置：${parts.join(' / ')}，其余默认执行前审批` : '\n审批配置：全部默认执行前审批'
}
const mcpServerTitle = (server: PluginMcpServerInfo) => {
  const base = `${server.id} · ${server.transport}${server.url ? ` · ${server.url}` : ''}${mcpApprovalSummary(server)}`
  const admission = server.admission
  if (!admission) return base
  if (!admission.ok) return `${base}\n未通过准入：${admission.errors.join('；')}`
  return admission.warnings.length ? `${base}\n准入通过（告警：${admission.warnings.join('；')}）` : base
}

// 服务端分页（口径与定时任务一致）
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

const loadPlugins = async () => {
  loading.value = true
  try {
    const res = await getAdminPlugins({
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value
    })
    if (res.status === 'success') {
      plugins.value = res.data.plugins || []
      total.value = res.data.total || 0
      hasLoaded.value = true
    } else {
      ElMessage.error(res.msg || '获取插件列表失败')
    }
  } catch (err) {
    console.error('获取插件列表失败:', err)
    ElMessage.error('获取插件列表失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

const handlePageChange = async () => {
  await loadPlugins()
}

const handleSizeChange = async () => {
  currentPage.value = 1
  await loadPlugins()
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadPlugins()
}

const handleUploadChange = async (uploadFile: UploadFile) => {
  const file = uploadFile.raw
  if (!file) return
  uploadRef.value?.clearFiles()
  if (!file.name.toLowerCase().endsWith('.zip')) {
    ElMessage.warning('请选择 .zip 插件包')
    return
  }
  if (file.size > PLUGIN_UPLOAD_MAX_BYTES) {
    ElMessage.warning(`插件包超过大小上限 ${PLUGIN_UPLOAD_MAX_BYTES / (1024 * 1024)}MB`)
    return
  }
  installing.value = true
  try {
    const res = await installPlugin(file)
    if (res.status === 'success') {
      const installed = res.data.plugin
      ElMessage.success(`插件 ${installed.display_name || installed.name} 安装成功，对新会话生效`)
      await loadPlugins()
    } else {
      ElMessage.error(res.msg || '安装插件失败')
    }
  } catch (err) {
    console.error('安装插件失败:', err)
    ElMessage.error('安装插件失败，请稍后重试')
  } finally {
    installing.value = false
  }
}

const togglePlugin = async (plugin: PluginInfo, enabled: boolean) => {
  try {
    const res = await setAdminPluginEnabled(plugin.name, enabled)
    if (res.status === 'success') {
      ElMessage.success(enabled ? '插件已启用，对新会话生效' : '插件已禁用')
    } else {
      ElMessage.error(res.msg || '操作失败')
    }
  } catch (err) {
    console.error('切换插件状态失败:', err)
    ElMessage.error('操作失败，请稍后重试')
  } finally {
    await loadPlugins()
  }
}

const removePlugin = async (plugin: PluginInfo) => {
  try {
    await ElMessageBox.confirm(
      `卸载后插件目录将被删除，其中的技能与 MCP 工具声明同时失效（对新会话）。确定卸载 ${plugin.display_name || plugin.name}？`,
      '卸载插件确认',
      { type: 'warning', confirmButtonText: '卸载', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await uninstallPlugin(plugin.name)
    if (res.status === 'success') {
      ElMessage.success('插件已卸载')
      await loadPlugins()
    } else {
      ElMessage.error(res.msg || '卸载插件失败')
    }
  } catch (err) {
    console.error('卸载插件失败:', err)
    ElMessage.error('卸载插件失败，请稍后重试')
  }
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 工具栏 -->
    <AdminPanelToolbar>
      <template #actions>
        <el-upload
          ref="uploadRef"
          action="#"
          accept=".zip"
          :auto-upload="false"
          :show-file-list="false"
          @change="handleUploadChange"
        >
          <el-button type="primary" size="small" round :loading="installing">
            <i class="fa-solid fa-plug" style="margin-right: 4px; font-size: 10px;"></i>
            安装插件
          </el-button>
        </el-upload>
        <el-button size="small" :loading="loading" @click="loadPlugins">
          <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
        </el-button>
      </template>
    </AdminPanelToolbar>

    <!-- 插件列表：服务端分页 -->
    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <el-table
        :data="plugins"
        v-loading="loading"
        size="small"
        height="100%"
        style="width: 100%"
        empty-text="暂无插件，点击右上角“安装插件”上传 zip 包"
      >
        <el-table-column label="插件" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="flex items-center gap-2">
              <i class="fa-solid fa-puzzle-piece text-[11px] text-blue-500"></i>
              <div class="min-w-0">
                <div class="text-sm font-medium text-slate-800">{{ row.display_name || row.name }}</div>
                <div class="text-[10px] text-slate-400">{{ row.name }} · v{{ row.version }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.description || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="技能" min-width="150">
          <template #default="{ row }">
            <div v-if="(row.skills || []).length" class="flex flex-wrap gap-1">
              <el-tag v-for="skill in row.skills" :key="skill" size="small" type="info" effect="plain">{{ skill }}</el-tag>
            </div>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="MCP server" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <div v-if="(row.mcp_servers || []).length" class="flex flex-wrap gap-1">
              <el-tag
                v-for="server in row.mcp_servers"
                :key="server.id"
                size="small"
                effect="plain"
                :type="mcpServerTagType(server)"
                :title="mcpServerTitle(server)"
              >{{ mcpServerLabel(server) }}</el-tag>
            </div>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column label="创建人" width="100" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.create_by_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">
            <span class="text-xs text-slate-600">{{ row.create_time || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="70" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              size="small"
              @change="(value: any) => togglePlugin(row, !!value)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="danger" @click="removePlugin(row)">
              <i class="fa-solid fa-trash-can text-[11px]" style="margin-right: 4px;"></i>卸载
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="flex shrink-0 justify-end pt-2">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          size="small"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </div>
  </div>
</template>
