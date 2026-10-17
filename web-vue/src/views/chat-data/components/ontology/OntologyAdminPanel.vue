<script setup lang="ts">
/**
 * 后台管理 · 本体管理面板。
 * 元数据管理下的「本体」类型容器，直接承载对象面板：
 * 树导航（根域 → 业务领域 → 对象/关系 → 属性/映射分组）+ 右键菜单完成全部治理操作。
 * 关系无独立页签——关系是对象之间的边，挂在领域下的「关系」分组统一管理。
 */
import { ref } from 'vue'
import OntologyObjectPanel from './OntologyObjectPanel.vue'

const objectPanelRef = ref<InstanceType<typeof OntologyObjectPanel> | null>(null)

// 宿主（MetaManagementPanel）切换类型页签时会调 ensureLoaded：转发给对象面板（未加载才拉取）
const ensureLoaded = async () => {
  await objectPanelRef.value?.ensureLoaded()
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="h-full min-h-0 overflow-hidden">
    <OntologyObjectPanel ref="objectPanelRef" class="h-full" />
  </div>
</template>
