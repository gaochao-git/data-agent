<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
const title = route.meta?.title || '功能开发中'
</script>

<template>
  <div class="coming-soon">
    <div class="content">
      <i class="fa-solid fa-hammer icon"></i>
      <h2>{{ title }}</h2>
      <p>功能开发中，敬请期待...</p>
    </div>
  </div>
</template>

<style scoped>
.coming-soon {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
}

.content {
  text-align: center;
  color: #9ca3af;
}

.icon {
  font-size: 64px;
  margin-bottom: 24px;
  color: #d1d5db;
}

h2 {
  font-size: 24px;
  font-weight: 600;
  color: #4b5563;
  margin: 0 0 12px;
}

p {
  font-size: 14px;
  margin: 0;
}
</style>
