<script setup lang="ts">
/**
 * 后台管理面板统一顶区：可选说明条（#hint 插槽）+ 统一节奏的工具栏行。
 * 默认插槽放筛选/搜索/刷新等工具，#actions 插槽放右侧主动作（新建/安装等），
 * 右对齐由本组件负责，面板内不要再手写 flex-1 占位或自定义工具栏内外边距。
 * 各后台面板顶区一律复用本组件（DataSourceManager / GlobalEnumAdminPanel /
 * OntologyObjectPanel / AgentScheduleAdminPanel / PluginAdminPanel /
 * SettingAdminPanel），新增面板也不例外。
 */
</script>

<template>
  <div class="shrink-0">
    <div v-if="$slots.hint" class="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
      <slot name="hint" />
    </div>
    <div class="flex flex-wrap items-center gap-2 px-4 py-2">
      <slot />
      <div v-if="$slots.actions" class="ml-auto flex items-center gap-2">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>
