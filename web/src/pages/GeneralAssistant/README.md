# 通用助手页面

AI辅助通用助手页面，提供统一的对话式交互界面。

## 页面结构

```
GeneralAssistant/
├── components/           # 组件目录
│   ├── Conversation.tsx      # 对话主界面
│   ├── HistorySidebar.tsx    # 历史对话侧边栏
│   └── ToolGrid.tsx          # 工具网格展示
├── hooks/               # 自定义Hooks（待实现）
├── utils/               # 工具函数（待实现）
├── constants.ts         # 常量配置
├── types.ts            # 类型定义
├── Layout.tsx          # 主布局
└── index.tsx           # 入口文件
```

## 主要功能

### 1. 历史对话侧边栏
- 展示历史对话列表
- 支持折叠/展开
- 新建会话按钮
- 会话删除功能（待实现）

### 2. 对话界面
- 模型选择（deepseek-r1、深度思考、法律搜索）
- 工具分类筛选（全部、投诉举报、执法检查、专家系统）
- 5个工具快捷卡片
- 消息流式输出
- Markdown渲染支持

### 3. 工具系统
当前支持的工具分类：
- **投诉举报**：投诉分析、投诉回复
- **执法检查**：事实认定、检查报告、整改通知
- **专家系统**：法律咨询、案例分析

## 访问路由

```
http://localhost:3000/general-assistant
```

## 待实现功能

### 后端集成
- [ ] 连接后端聊天API
- [ ] 实现流式输出
- [ ] 历史对话加载
- [ ] 文件上传功能

### 功能增强
- [ ] 会话删除
- [ ] 会话重命名
- [ ] 工具参数配置
- [ ] 消息编辑/重新生成
- [ ] 复制/分享功能
- [ ] 暗黑模式适配

### 性能优化
- [ ] 虚拟滚动（长对话优化）
- [ ] 消息缓存
- [ ] 懒加载历史记录

## 设计参考

UI设计参考了现有的执法检查页面，主要特点：
- 简洁的三栏布局（历史-对话-工具）
- 模型选择器放在输入框上方
- 工具分类采用标签页形式
- 工具卡片采用网格布局

## 技术栈

- React 18
- TypeScript
- Tailwind CSS
- Framer Motion（动画）
- React Markdown（消息渲染）

## 开发说明

### 添加新工具

1. 在 `constants.ts` 中添加工具定义：
```typescript
{
  id: "new-tool",
  name: "新工具",
  description: "工具描述",
  category: "inspection" // 或其他分类
}
```

2. 在 `ToolGrid` 组件中工具会自动显示

### 添加新模型

在 `constants.ts` 中添加：
```typescript
{
  id: "new-model",
  name: "新模型",
  description: "模型描述"
}
```

### 自定义样式

所有样式使用 Tailwind CSS，可以在组件中直接修改类名。
主题色为 indigo（蓝紫色），可在 `className` 中统一替换。
