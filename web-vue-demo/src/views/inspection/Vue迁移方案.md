我需要将 inspection 执法检查模块从 React 迁移到 Vue3，保证样式完全一致。chat-data模块已完成迁移，位于 web-vue/src/views/chat-data（不用参考内容，仅参考路径目录结构）

详细迁移方案（十步骤迁移）：
  一、迁移目标与范围
  原模块：web/src/pages/Inspection
  目标模块：web-vue/src/views/inspection
  包含 5 个子功能：事实分析、检查报告撰写、整改通知书撰写、信息简表撰写、证据目录撰写
  必须满足：
  - 后端接口、请求参数、响应格式、SSE 事件格式不变
  - 页面样式、布局结构、组件架构与 React 版保持一致
  - 保持 Embedded 模式，供 GeneralAssistant 嵌入
  - 不重复实现总入口已有的 topbar、历史 panel、图标切换壳子
  不在本次范围：
  - web\src\pages\GeneralAssistant 总入口迁移

  二、现有 React 模块结构
  核心文件：
  - 入口：index.tsx
  - 主布局：EmbeddedLayout.tsx
  - 主状态 Hook：hooks/useViolationAnalysis.ts
  - SSE Hook：hooks/useSSEStream.ts
  - 左侧对话区：components/EmbeddedConversation.tsx
  - 右侧控制台：components/Console.tsx
  - 类型：types.ts
  - 常量：constants.ts
  - 工具函数：utils/

  迁移时保持以下结构不变：
  - 单一主状态核心
  - EmbeddedLayout 负责编排和左右栏切换
  - EmbeddedConversation 负责左侧交互
  - Console 负责右侧预览与审核编辑
  - 5 个子功能通过 agentId 切换，不拆成5套页面
  
  三、Vue 目标目录结构
  在 web-vue/src/views/inspection 下建立：
  web-vue/src/views/inspection/
  ├── api/
  │   └── inspection.ts
  ├── components/
  │   ├── EmbeddedConversation.vue
  │   ├── Console.vue
  │   ├── TaskManage.vue
  │   ├── FileUploadZone.vue
  │   ├── DocumentViewer.vue
  │   ├── AttachmentViewer.vue
  │   ├── CaseReportViewer.vue
  │   ├── ReviewEditor.vue
  │   ├── EditableAnalysisView.vue
  │   ├── AnalysisProgressMessage.vue
  │   ├── AnalysisResultMessage.vue
  │   ├── DocumentCard.vue
  │   ├── ViolationFileManagementModal.vue
  │   ├── ViolationFileGroupsDisplay.vue
  │   ├── LawContentPopover.vue
  │   └── index.ts
  ├── composables/
  │   ├── useViolationAnalysis.ts
  │   ├── useSSEStream.ts
  │   └── index.ts
  ├── types/
  │   └── index.ts
  ├── utils/
  │   ├── dataMapper.ts
  │   ├── formatters.ts
  │   ├── messageParser.ts
  │   ├── streamHelpers.ts
  │   └── index.ts
  ├── views/
  │   └── index.vue
  ├── EmbeddedLayout.vue
  ├── constants.ts
  ├── index.ts
  └── README.md

  要求：
  - inspection 内部文件职责与 React 同名、同层级优先
  - 新增 README.md
  - 不重组组件职责
  - 不把多个核心组件合并进一个页面文件
  - 不为了“Vue 化”重做结构

  四、迁移顺序及详细要求

  按以下顺序执行：

  步骤一. 迁移类型、常量、工具函数
  - types.ts -> types/index.ts
  - constants.ts -> constants.ts
  - utils/dataMapper.ts
  - utils/formatters.ts
  - utils/messageParser.ts
  - utils/streamHelpers.ts
  要求：
  - 类型字段名保持不变
  - 中文业务字段保持不变
  - 不重命名后端结构字段
  - 输出结果必须与 React 版一致
  
  
  步骤二. 迁移 API 封装和 SSE 逻辑
  2.1 API层迁移
  新增web-vue/src/views/inspection/api/inspection.ts
  统一封装以下请求：
  - 创建会话
  - 获取会话消息
  - 获取会话状态
  - 获取会话文件列表
  - 上传文件
  - 删除文件组 / 删除文件
  - 会话补全请求
  - 会话恢复请求
  - 生成标题
  - 文件下载地址生成
  要求：
  - URL、method、body、credentials 与 React 保持一致
  - 不改字段命名
  - SSE 请求可继续使用原生 fetch
  
  2.2 SSE迁移
  源文件：hooks/useSSEStream.ts
  目标文件：web-vue/src/views/inspection/composables/useSSEStream.ts
  必须保留：
  - event: + data: 解析
  - [DONE] 结束处理
  - completed
  - error
  - interrupt
  - task_progress
  - 旧格式兼容
  - AbortController 中止能力
  要求：
  - 先按 React 逻辑等价迁移
  - 不改事件语义
  - 不把 SSE 解析逻辑写进组件
  - 禁止先引入Pinia重构状态
  
  
  步骤三. 迁移 useViolationAnalysis
  源文件：hooks/useViolationAnalysis.ts
  目标文件：web-vue/src/views/inspection/composables/useViolationAnalysis.ts
  映射规则：
  - useState -> ref / reactive
  - useRef -> ref
  - useMemo -> computed
  - useEffect -> watch / watchEffect / onMounted / onUnmounted
  - useCallback -> 普通函数
  要求：
  - 所有业务状态集中在主 composable
  - 所有副作用集中在主 composable
  - 子组件只消费状态和触发事件
  - 不把线程加载、SSE、审核恢复这类逻辑下沉到子组件
  表单处理：
  - 保留 zod
  - 使用 reactive(formData) + zod.safeParse
  - 单独维护错误对象
  - 不额外引入复杂表单框架
  主composable内必须保留：
  - 会话加载
  - 创建线程
  - 发送消息
  - 流式接收
  - 报告生成
  - 审核恢复
  - 标题生成
  - 历史刷新触发
  - 文档预览相关基础状态
  - 文件状态恢复
  
  
  步骤四. 迁移 EmbeddedLayout
   源文件：EmbeddedLayout.tsx
   目标文件：web-vue/src/views/inspection/EmbeddedLayout.vue
   组件建议接口：
   // props
	agentId?: string
	initialThreadId?: string
	// emits
	conversation-id-change
	history-refresh
	title-change
  职责必须保留：
  - 初始化 useViolationAnalysis
  - 接收 agentId 与 initialThreadId
  - 管理右侧预览状态
  - 管理左右面板宽度拖拽
  - 向父级同步标题、会话 ID、历史刷新
  - 管理新建线程与切换线程后的状态收口
  要求：
  - 左侧只挂 EmbeddedConversation
  - 右侧只挂 Console
  - 打开某类预览时，必须清空其他预览状态
  - 所有对外事件都从这一层发出
  
  
  步骤五. 迁移 EmbeddedConversation
  源文件： components/EmbeddedConversation.tsx
  目标： web-vue/src/views/inspection/components/EmbeddedConversation.vue
  必须保留的职责：
  - 欢迎态显示
  - 消息列表渲染
  - Markdown 渲染
  - 任务进度显示
  - 输入框发送与停止
  - 文件上传
  - 报告类型切换
  - 基于 agentId 设置输入区初始状态
  - 文件分组管理
  - 检查报告上传与复用
  - 消息点击触发右侧预览
  - 自动滚动与用户滚动状态控制
  要求：
  - 组件只负责左侧 UI 与交互
  - API 调用和状态写入尽量走 composable
  - 文件上传前确保线程存在的逻辑统一封装
  
  
  步骤六. 迁移 Console
  源文件：components/Console.tsx
  目标文件：web-vue/src/views/inspection/components/Console.vue
  职责保持不变：
  - 审核编辑器优先显示
  - 其次显示案例报告
  - 否则显示文档预览器
  - close 和 regenerate 行为交回父级处理
  要求：
  - 不在 Console 内部持有复杂业务状态
  - threadId 继续透传下去
  
  
  步骤七. 迁移其余公共组件
  先按模块内复用迁移，不做全局抽象。
  7.1. 纯展示组件直接迁移：
  - TaskManage
  - DocumentCard
  - AnalysisProgressMessage
  - AnalysisResultMessage
  7.2. 中等交互组件保留 props/emits：
  - FileUploadZone
  - LawContentPopover
  - ViolationFileGroupsDisplay
  7.3. 强业务组件原样迁移：
  - ReviewEditor
  - DocumentViewer
  - ViolationFileManagementModal
  - EditableAnalysisView
  要求：
  - 每个组件先核对输入输出
  - 不改变组件职责
  - 不提前抽成跨模块公共组件
  
  
  步骤八. 接入独立路由
  当前 Vue 路由：web-vue/src/router/index.ts
  迁移后需要：
  - 将 /inspection 路由替换为 @/views/inspection/views/index.vue
  - 在 inspection/index.ts 中导出 inspectionRoutes
  要求：
  - views/index.vue 只作为独立页面壳子
  - 页面内部仍使用 EmbeddedLayout.vue
  - 嵌入和独立访问共用同一套核心逻辑
  
  
  步骤九. 导出嵌入式组件供 generalassistant 接入
  在整个页面中，TopNavBar导航栏（主页、新建会话、历史）等公用Layout是其他同事负责迁移，迁移前的代码在web\src\pages\GeneralAssistant，但迁移后的代码只有空目录。
  需要注意，历史面板的需求和迁移前稍有不同，在Vue版本中，历史面板单独拆出来了，独立于所有子功能界面（如事实认定分析或文书撰写界面），没有任何嵌入关系，直接从上层入口GeneralAssistant的TopNavBar进入。
  导出文件：web-vue/src/views/inspection/index.ts
  导出内容：
  - inspectionRoutes
  - EmbeddedInspectionLayout
  - InspectionView
  - 必要类型
  接入原则：
  - 总入口负责模块切换、topbar、历史 panel
  - inspection 负责模块内部线程、消息流、文件、预览、审核恢复
  - 总入口切到 inspection 某子功能时，仅传对应 agentId
  - 打开历史会话时，再传 initialThreadId
  
  
  步骤十. 完整迁移/联调 5 个子功能
  5 个子功能共用同一套嵌入布局和主 composable，通过 agentId 切换：
	fact_analysis	事实分析
	report_jcbg	检查报告
	report_zgtzs	整改通知书
	report_zjml	证据目录
	report_xxjb	信息简报
  可能需要参考web\src\pages\GeneralAssistant\agentConfig路径下inspect开头的ts文件
  要求：
  - 欢迎语、上传要求、报告类型映射与 React 保持一致
  - 总体仍是一套 inspection 模块

  五、开发执行要求
  1. 先读原 React 文件，再迁移对应 Vue 文件。
  2. 每迁移一个组件，先确认其输入、输出、副作用。
  3. 后端接口路径、字段名、事件类型保持不变。
  4. agentId 与报告子类型映射保持不变。
  5. inspection 内部状态不提前全局化。
  6. 不抽象成通用 AI 对话框架。
  7. 审核、文件分组、报告再生成逻辑必须一并迁移。
  8. 先保证等价运行，再做代码整理。
  9. 随时更新README.md，记录任务完成情况和进度，并帮助迁移中断恢复进度。
  10. 每完成一层迁移，立即单独联调。