<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { installOverlayClickGuard } from './utils/overlayClickGuard'

onMounted(() => {
  installOverlayClickGuard()
})
</script>

<template>
  <RouterView />
</template>

<style>
/* 全局样式已在 main.css 中定义 */
</style>
