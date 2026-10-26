<script setup lang="ts">
/**
 * Pipeline 独立后台页：复用 DatasetAdminPanel 的 fixed-view 模式，
 * 只渲染 Pipeline Builder（列表/画布/节点配置/运行记录），不再出现数据集切换条。
 */
import DatasetAdminPanel from './DatasetAdminPanel.vue'
</script>

<template>
  <DatasetAdminPanel view="pipelines" class="h-full" />
</template>
