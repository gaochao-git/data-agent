<script setup lang="ts">
/**
 * 后台管理 · 插件面板。
 * 已安装插件列表 + zip 安装 + 启停 + 卸载；安装/启停对新会话生效（接口语义见 plugins/README.md）。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadInstance } from 'element-plus'
import { downloadMcpServerTemplate, downloadPluginTemplate, getAdminPlugins, installPlugin, probeAdminPluginMcpServerTools, setAdminPluginEnabled, uninstallPlugin } from '../api'
import type { PluginInfo, PluginMcpServerInfo, PluginMcpToolInfo } from '../types'
import AdminPanelToolbar from './AdminPanelToolbar.vue'

// 与后端 installer 的 zip 上限一致（50MB）
const PLUGIN_UPLOAD_MAX_BYTES = 50 * 1024 * 1024

const loading = ref(false)
const plugins = ref<PluginInfo[]>([])
const hasLoaded = ref(false)
const uploadRef = ref<UploadInstance | null>(null)
const installing = ref(false)
const downloadingTemplate = ref(false)
const downloadingMcpTemplate = ref(false)
const probeVisible = ref(false)
const probeLoading = ref(false)
const probeError = ref('')
const probePluginName = ref('')
const probeServerId = ref('')
const probeTools = ref<PluginMcpToolInfo[]>([])
const selectedProbeTool = ref<PluginMcpToolInfo | null>(null)
const selectedProbeToolParametersJson = computed(() =>
  JSON.stringify(selectedProbeTool.value?.parameters || [], null, 2)
)

// MCP server 准入状态展示：未通过标红并给出原因，通过但有告警也提示
const mcpServerLabel = (server: PluginMcpServerInfo) =>
  server.admission && !server.admission.ok ? `${server.id} ⚠` : server.id
// 每工具审批配置摘要：管理员固定 ask/allow 两档，用户不可切换
const mcpApprovalSummary = (server: PluginMcpServerInfo) => {
  const entries = Object.entries(server.tool_approvals || {})
  if (!entries.length) return '\n审批配置：全部默认执行前审批'
  const counts = { ask: 0, allow: 0, deny: 0 } as Record<string, number>
  entries.forEach(([, value]) => {
    if (value in counts) counts[value] = (counts[value] ?? 0) + 1
  })
  const parts = [
    counts.ask ? `${counts.ask} 执行前审批` : '',
    counts.allow ? `${counts.allow} 免审批` : '',
    counts.deny ? `${counts.deny} 旧 deny 跳过` : ''
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

const handleDownloadTemplate = async () => {
  downloadingTemplate.value = true
  try {
    const blob = await downloadPluginTemplate()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'chatdata-plugin-template.zip'
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    console.error('下载插件模板失败:', err)
    ElMessage.error('下载插件模板失败，请稍后重试')
  } finally {
    downloadingTemplate.value = false
  }
}

const handleDownloadMcpTemplate = async () => {
  downloadingMcpTemplate.value = true
  try {
    const blob = await downloadMcpServerTemplate()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'chatdata-mcp-server-template.zip'
    link.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    console.error('下载 MCP Server 模板失败:', err)
    ElMessage.error('下载 MCP Server 模板失败，请稍后重试')
  } finally {
    downloadingMcpTemplate.value = false
  }
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

const probeMcpTools = async (plugin: PluginInfo, server: PluginMcpServerInfo) => {
  probeVisible.value = true
  probeLoading.value = true
  probeError.value = ''
  probePluginName.value = plugin.name
  probeServerId.value = server.id
  probeTools.value = []
  selectedProbeTool.value = null
  try {
    const res = await probeAdminPluginMcpServerTools(plugin.name, server.id)
    if (res.status === 'success') {
      probeTools.value = res.data.tools || []
    } else {
      probeError.value = res.msg || '探测失败'
    }
  } catch (err) {
    console.error('探测 MCP 工具失败:', err)
    probeError.value = '连接 MCP server 失败，请检查地址、认证和网络'
  } finally {
    probeLoading.value = false
  }
}

const openProbeTool = (tool: PluginMcpToolInfo) => {
  selectedProbeTool.value = tool
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 工具栏 -->
    <AdminPanelToolbar>
      <template #actions>
        <el-button size="small" :loading="downloadingTemplate" @click="handleDownloadTemplate">
          <i class="fa-solid fa-download" style="margin-right: 4px; font-size: 10px;"></i>
          下载插件模板
        </el-button>
        <el-button size="small" :loading="downloadingMcpTemplate" @click="handleDownloadMcpTemplate">
          <i class="fa-solid fa-server" style="margin-right: 4px; font-size: 10px;"></i>
          下载 MCP 模板
        </el-button>
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
        <el-table-column label="MCP server" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <div v-if="(row.mcp_servers || []).length" class="flex min-w-0 flex-wrap gap-1">
              <el-button
                v-for="server in row.mcp_servers"
                :key="server.id"
                size="small"
                plain
                :type="server.admission && !server.admission.ok ? 'danger' : 'primary'"
                :title="`${mcpServerTitle(server)}\n点击探测工具`"
                class="!h-6 !px-2 font-mono text-[11px]"
                @click.stop="probeMcpTools(row, server)"
              >{{ mcpServerLabel(server) }}</el-button>
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
    <el-dialog v-model="probeVisible" title="MCP server 工具探测" width="900px" destroy-on-close>
      <div class="mb-2 flex items-center justify-between gap-3">
        <span class="text-xs text-slate-500">
          {{ probePluginName }} / {{ probeServerId }}
          <template v-if="selectedProbeTool">
            / <span class="font-mono font-medium text-slate-800">{{ selectedProbeTool.name }}</span>
            <el-tag
              size="small"
              class="ml-1 align-middle"
              :type="selectedProbeTool.approval === 'ask' ? 'warning' : 'success'"
              effect="plain"
            >
              {{ selectedProbeTool.approval === 'ask' ? 'ask' : 'allow' }}
            </el-tag>
          </template>
        </span>
        <el-button
          v-if="selectedProbeTool"
          link
          type="primary"
          size="small"
          @click="selectedProbeTool = null"
        >
          <i class="fa-solid fa-arrow-left text-[11px] mr-1"></i>返回工具列表
        </el-button>
      </div>
      <div v-if="probeLoading" class="py-8 text-center text-sm text-slate-500">
        <i class="fa-solid fa-spinner fa-spin mr-2"></i>正在连接并探测工具…
      </div>
      <el-alert v-else-if="probeError" :title="probeError" type="error" :closable="false" show-icon />
      <template v-else-if="selectedProbeTool">
        <p class="mb-3 text-xs leading-5 text-slate-600">{{ selectedProbeTool.description || '无描述' }}</p>
        <div class="overflow-hidden rounded border border-slate-200 bg-slate-50">
          <div class="flex items-center justify-between border-b border-slate-200 px-3 py-1.5">
            <span class="text-[11px] font-medium text-slate-500">参数 JSON</span>
            <span class="text-[11px] text-slate-400">{{ (selectedProbeTool.parameters || []).length }} 个参数</span>
          </div>
          <pre class="max-h-[360px] overflow-auto p-3 text-xs leading-5 text-slate-700"><code>{{ selectedProbeToolParametersJson }}</code></pre>
        </div>
      </template>
      <el-empty v-else-if="!probeTools.length" description="该 server 没有返回工具" />
      <el-table
        v-else
        :data="probeTools"
        size="small"
        border
        max-height="420"
      >
        <el-table-column label="工具名" width="190">
          <template #default="{ row: tool }">
            <span class="font-mono text-xs text-slate-700">{{ tool.name }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="320" show-overflow-tooltip>
          <template #default="{ row: tool }">
            <span class="text-xs text-slate-600">{{ tool.description || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="审批" width="70" align="center">
          <template #default="{ row: tool }">
            <el-tag size="small" :type="tool.approval === 'ask' ? 'warning' : 'success'" effect="plain">
              {{ tool.approval === 'ask' ? 'ask' : 'allow' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="参数" width="70" align="center">
          <template #default="{ row: tool }">
            <span class="text-xs text-slate-500">{{ tool.parameters?.length || 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row: tool }">
            <el-button link type="primary" size="small" @click.stop="openProbeTool(tool)">
              查看参数
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="probeVisible = false">关闭</el-button>
      </template>
    </el-dialog>

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
