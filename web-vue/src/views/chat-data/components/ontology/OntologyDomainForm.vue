<script setup lang="ts">
/**
 * 本体领域创建/编辑弹窗：领域=业务域实体，一对一绑定数据源组。
 * 对象挂领域后继承其组（组决策从对象/关系层上移到领域层，一次绑定即可）。
 * 领域 API 名创建后不可改；编辑活跃领域提交 allow_overwrite=true。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getAdminDatasources, getAdminOntologyDomains, upsertAdminOntologyDomain } from '../../api'
import type { DataSource } from '../../types'
import { ONTOLOGY_NAME_PATTERN } from './ontologyMeta'

const props = defineProps({
  // 编辑的领域 API 名；空串表示新建
  domainName: { type: String, default: '' }
})

const emit = defineEmits<{ saved: [] }>()

const visible = defineModel<boolean>('visible', { default: false })

const loading = ref(false)
const saving = ref(false)
const editing = computed(() => !!props.domainName)

const formName = ref('')
const formDisplayName = ref('')
const formGroupId = ref('')
const formDescription = ref('')

// 数据源组下拉（领域一对一绑定的组）
const groups = ref<DataSource[]>([])
const groupsLoading = ref(false)

const loadGroups = async () => {
  groupsLoading.value = true
  try {
    const res = await getAdminDatasources()
    if (res.status === 'success') {
      groups.value = res.data?.datasources || []
    } else {
      ElMessage.error(res.msg || '获取数据源组失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取数据源组失败:', err)
  } finally {
    groupsLoading.value = false
  }
}

const resetForm = () => {
  formName.value = ''
  formDisplayName.value = ''
  formGroupId.value = ''
  formDescription.value = ''
}

const openLoad = async () => {
  resetForm()
  void loadGroups()
  if (!props.domainName) return
  loading.value = true
  try {
    // 领域无单查接口，从列表接口取（公司级量级有限）
    const res = await getAdminOntologyDomains()
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '获取领域详情失败')
      visible.value = false
      return
    }
    const detail = (res.data?.domains || []).find(item => item.domain_name === props.domainName)
    if (!detail) {
      ElMessage.error(`领域不存在: ${props.domainName}`)
      visible.value = false
      return
    }
    formName.value = detail.domain_name
    formDisplayName.value = detail.display_name
    formGroupId.value = detail.group_id
    formDescription.value = detail.description
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取领域详情失败:', err)
    visible.value = false
  } finally {
    loading.value = false
  }
}

watch(visible, value => {
  if (value) void openLoad()
})

const saveDomain = async () => {
  const name = formName.value.trim()
  if (!name) {
    ElMessage.warning('请填写领域 API 名')
    return
  }
  if (!ONTOLOGY_NAME_PATTERN.test(name)) {
    ElMessage.warning('领域 API 名仅允许字母、数字、下划线，长度 1-128')
    return
  }
  if (!formGroupId.value) {
    ElMessage.warning('请选择绑定的数据源组')
    return
  }
  saving.value = true
  try {
    const res = await upsertAdminOntologyDomain(name, {
      group_id: formGroupId.value,
      display_name: formDisplayName.value.trim(),
      description: formDescription.value.trim(),
      allow_overwrite: editing.value
    })
    if (res.status === 'success') {
      ElMessage.success('保存成功')
      visible.value = false
      emit('saved')
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如组不存在、重名），不再重复提示
    console.error('保存本体领域失败:', err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" width="min(560px, 92vw)" :append-to-body="true" :close-on-click-modal="false">
    <template #header>
      <span class="text-base font-medium text-slate-800">
        {{ editing ? `编辑业务领域：${domainName}` : '新建业务领域' }}
      </span>
    </template>

    <div v-loading="loading" class="flex flex-col gap-3">
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">领域 API 名（创建后不可改）</div>
          <el-input v-model="formName" size="small" :disabled="editing" placeholder="仅字母、数字、下划线，如 ecommerce_trade" class="font-mono" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">展示名</div>
          <el-input v-model="formDisplayName" size="small" maxlength="128" placeholder="用户可见名称，如 电商交易" />
        </div>
      </div>
      <div>
        <div class="mb-1 text-xs text-slate-500">绑定数据源组（一次绑定，对象挂领域后继承其组）</div>
        <el-select
          v-model="formGroupId"
          size="small"
          :loading="groupsLoading"
          filterable
          placeholder="选择数据源组"
          class="!w-full"
        >
          <el-option v-for="ds in groups" :key="ds.id" :label="`${ds.group_name} (${ds.id})`" :value="ds.id" />
        </el-select>
      </div>
      <div>
        <div class="mb-1 text-xs text-slate-500">描述</div>
        <el-input
          v-model="formDescription"
          type="textarea"
          :rows="3"
          maxlength="512"
          show-word-limit
          placeholder="领域业务含义（进入 agent 上下文）"
        />
      </div>
    </div>

    <template #footer>
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button type="primary" size="small" :loading="saving" @click="saveDomain">保存</el-button>
    </template>
  </el-dialog>
</template>
