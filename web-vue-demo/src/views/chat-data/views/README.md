# 智能问数 - 嵌入指南

三种方式接入智能问数，按需选择。

## 页面模式

直接作为路由页面使用，配置路由指向 `index.vue` 即可，这是智能问数的完整页面。

```ts
{
  path: '/chatdata',
  component: () => import('@/views/chat-data/views/index.vue')
}
```

## 弹窗模式

点击按钮弹出全屏对话框，关闭后回到原页面。适合大多数场景。

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { ChatDataDialog } from '@/views/chat-data'

const show = ref(false)
</script>

<template>
  <el-button @click="show = true">智能问数</el-button>

  <ChatDataDialog
    v-model:visible="show"
    :initial-width="1200"
    :initial-height="760"
    :options="{ parentComponentName: '举报管理' }"
  />
</template>
```

## 悬浮窗模式

可拖动、可缩放的悬浮窗口，适合边操作边查询。用自己的按钮触发打开。

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { ChatDataFloat } from '@/views/chat-data'

const floatRef = ref<InstanceType<typeof ChatDataFloat> | null>(null)
</script>

<template>
  <el-button @click="floatRef?.open()">智能问数</el-button>

  <ChatDataFloat
    ref="floatRef"
    :show-fab="false"
    :options="{ parentComponentName: '执法检查', hideBuilderChartPanel: false }"
  />
</template>
```

## options 参数说明
| 参数 | 说明 | 示例 |
|------|------|------|
| `parentComponentName` | 你的模块名，用于自动匹配数据源 | `'举报管理'`、`'执法检查'` |
| `defaultMode` | 打开后默认显示哪个页面 | `'conversation'`（对话）、`'builder'`（查询构建）、`'editor'`（SQL编辑） |
| `defaultDashboardName` | 默认打开的看板名称（仅 `dashboard` 模式生效，不传默认第一个） | `'某电商人力资源分析'` |
| `modeLabels` | 自定义四个主模式名称（四个必须全传） | `{ conversation:'数据智能体', builder:'可视化查询', editor:'SQL查询', dashboard:'数据看板' }` |
| `initialSql` | SQL 查询页初始化 SQL | `'SELECT * FROM table LIMIT 20'` |
| `initialBuilderConfig` | 可视化查询初始化配置（恢复拖拽编排） | `{ mainTable, dimensions, aggregations, ... }` |
| `onSqlChange` | SQL 查询/可视化查询点击“保存SQL”后的回调 | `(payload) => console.log(payload.sql)` |
| `hideTabs` | 隐藏指定主模式 tab（支持 key 或当前展示名称） | `['dashboard']`、`['数据看板']` |
| `hideBuilderChartPanel` | 隐藏可视化中部“图表/分析”面板，默认 `false` | `true` |

都是可选的，不传就用默认值。`parentComponentName` 需要先在数据源管理中配置模块映射才能自动匹配。

未传 `parentComponentName` 时，组件仍会正常展示，但不会显示任何数据源；该规则对页面模式、弹窗模式、悬浮窗模式都生效。

`parentComponentName` 也支持通过 URL query 传入：
- 示例：`/chatdata?parentComponentName=举报管理`
- 优先级：`options.parentComponentName` 高于 URL query

## Tab 命名联动示例

```vue
<ChatDataFloat
  ref="floatRef"
  :show-fab="false"
  :options="{
    parentComponentName: '执法检查',
    defaultMode: 'dashboard',
    defaultDashboardName: '某电商人力资源分析',
    modeLabels: {
      conversation: '数据智能体',
      builder: '可视化',
      editor: 'SQL',
      dashboard: '数据看板'
    },
    hideTabs: ['数据看板']
  }"
/>
```

说明：
- `modeLabels` 不传时使用默认：`数据智能体/可视化查询/SQL查询/数据看板`。
- 传 `modeLabels` 时四个必须全部传。
- `hideTabs` 支持 key 和名称两种写法（会按当前名称映射）。
