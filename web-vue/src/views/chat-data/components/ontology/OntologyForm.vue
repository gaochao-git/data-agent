<script setup lang="ts">
/**
 * 本体创建/编辑弹窗：本体=对象类型容器（对位 Palantir ontology），纯语义容器
 * 不绑数据源组（对象类型的取数后端由各自映射指向的语义模型决定，可跨组）。
 * 本体 API 名创建后不可改；编辑活跃本体提交 allow_overwrite=true。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getAdminOntologies, upsertAdminOntology } from '../../api'
import { ONTOLOGY_NAME_PATTERN } from './ontologyMeta'

const props = defineProps({
  // 编辑的本体 API 名；空串表示新建
  ontologyName: { type: String, default: '' }
})

const emit = defineEmits<{ saved: [] }>()

const visible = defineModel<boolean>('visible', { default: false })

const loading = ref(false)
const saving = ref(false)
const editing = computed(() => !!props.ontologyName)

const formName = ref('')
const formDisplayName = ref('')
const formDescription = ref('')

const resetForm = () => {
  formName.value = ''
  formDisplayName.value = ''
  formDescription.value = ''
}

const openLoad = async () => {
  resetForm()
  if (!props.ontologyName) return
  loading.value = true
  try {
    // 本体无单查接口，从列表接口取（公司级量级有限）
    const res = await getAdminOntologies()
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '获取本体详情失败')
      visible.value = false
      return
    }
    const detail = (res.data?.ontologies || []).find(item => item.ontology_name === props.ontologyName)
    if (!detail) {
      ElMessage.error(`本体不存在: ${props.ontologyName}`)
      visible.value = false
      return
    }
    formName.value = detail.ontology_name
    formDisplayName.value = detail.display_name
    formDescription.value = detail.description
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取本体详情失败:', err)
    visible.value = false
  } finally {
    loading.value = false
  }
}

watch(visible, value => {
  if (value) void openLoad()
})

const saveOntology = async () => {
  const name = formName.value.trim()
  if (!name) {
    ElMessage.warning('请填写本体 API 名')
    return
  }
  if (!ONTOLOGY_NAME_PATTERN.test(name)) {
    ElMessage.warning('本体 API 名仅允许字母、数字、下划线，长度 1-128')
    return
  }
  saving.value = true
  try {
    const res = await upsertAdminOntology(name, {
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
    // 拦截器已弹出具体错误原因（如重名），不再重复提示
    console.error('保存本体失败:', err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" width="min(560px, 92vw)" :append-to-body="true" :close-on-click-modal="false">
    <template #header>
      <span class="text-base font-medium text-slate-800">
        {{ editing ? `编辑本体：${ontologyName}` : '新增本体' }}
      </span>
    </template>

    <div v-loading="loading" class="flex flex-col gap-3">
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">本体 API 名（创建后不可改）</div>
          <el-input v-model="formName" size="small" :disabled="editing" placeholder="仅字母、数字、下划线，如 ecommerce" class="font-mono" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">展示名</div>
          <el-input v-model="formDisplayName" size="small" maxlength="128" placeholder="用户可见名称，如 电子商务" />
        </div>
      </div>
      <div class="rounded bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-500">
        本体不绑定数据源组：对象类型的取数后端由其映射指向的语义模型决定，同一本体下的对象可来自不同数据源组。
      </div>
      <div>
        <div class="mb-1 text-xs text-slate-500">描述</div>
        <el-input
          v-model="formDescription"
          type="textarea"
          :rows="3"
          maxlength="512"
          show-word-limit
          placeholder="本体业务含义（进入 agent 上下文）"
        />
      </div>
    </div>

    <template #footer>
      <el-button size="small" @click="visible = false">取消</el-button>
      <el-button type="primary" size="small" :loading="saving" @click="saveOntology">保存</el-button>
    </template>
  </el-dialog>
</template>
