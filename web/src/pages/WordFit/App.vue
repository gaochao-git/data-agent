<script setup>
import { ref } from 'vue'
import WordFitWindow from './components/WordFitWindow.vue'
import WordFitButton from './components/WordFitButton.vue'

// 页面数据
const fileId = ref("大米汽车举报信x.docx")
const extractFields = ref([
  { "msgkey": "evidance", "description": "证据" },
  { "msgkey": "amount", "description": "索赔金额" },
  { "msgkey": "parties", "description": "举报人" },
  {
    "msgkey": "agentList",
    "description": "被举报人列表",
    "type": "array",
    "items": [
      { "msgkey": "agent_name", "description": "被举报人姓名" },
      { "msgkey": "agent_address", "description": "被举报人地址" },
      { "msgkey": "agent_telno", "description": "被举报人电话" }
    ]
  }
])

// 结果数据 - 初始化为空对象，确保默认展示结果卡片
const resultData = ref({
  evidance: '',
  parties: '',
  amount: '',
  agentList: []
})

// 组件引用
const wordFitRef = ref(null)

// 启动提取 (旧版)
const handleStart = () => {
  if (wordFitRef.value) {
    wordFitRef.value.start()
  }
}

// 提取完成回调
const onExtractionComplete = (data) => {
  console.log('Extraction completed in App:', data)
  resultData.value = data
}
</script>

<template>
  <div class="app-container">
    <header class="app-header">
      <h1>WordFit Verification Page</h1>
    </header>

    <main class="app-main">
      <div class="content-wrapper">
        <!-- 左侧/上方：输入控制 - 紧凑布局 -->
        <div class="card input-card">
          <div class="input-row">
            <div class="form-group">
              <label>File ID:</label>
              <input v-model="fileId" type="text" class="input-field" />
            </div>
          </div>
          
          <!-- 按钮区域 -->
          <div class="action-row">
            <!-- 旧版弹窗模式 -->
            <button @click="handleStart" class="primary-btn ghost">
              Start (Modal)
            </button>
            
            <!-- 新版滚动窗口模式 -->
            <div class="new-component-wrapper">
               <WordFitButton 
                 :file-id="fileId"
                 :extract-fields="extractFields"
                 @extraction-complete="onExtractionComplete"
               />
            </div>
          </div>
        </div>

        <!-- 右侧/下方：结果展示 - 始终展示 -->
        <div class="card result-card">
          <h2>Extraction Results</h2>
          
          <div class="result-section">
            <!-- Row 1: Evidence (Full Width) -->
            <div class="result-row">
              <div class="result-item full-width-item">
                <label>Evidence (证据):</label>
                <div class="result-value">{{ resultData.evidance || '' }}</div>
              </div>
            </div>

            <!-- Row 2: Reporter and Amount (Side by Side) -->
            <div class="result-row two-col">
              <div class="result-item">
                <label>Reporter (举报人):</label>
                <div class="result-value">{{ resultData.parties || '' }}</div>
              </div>
              <div class="result-item">
                <label>Claim Amount (索赔金额):</label>
                <div class="result-value">{{ resultData.amount || '' }}</div>
              </div>
            </div>

            <!-- Row 3: Reported Persons (Full Width) -->
            <div class="result-row">
              <div class="result-item full-width-item">
                <label>Reported Persons (被举报人列表):</label>
                <div v-if="resultData.agentList && resultData.agentList.length > 0" class="table-container">
                  <table class="agent-table">
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Address</th>
                        <th>Tel</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="(agent, index) in resultData.agentList" :key="index">
                        <td>{{ agent.agent_name }}</td>
                        <td>{{ agent.agent_address }}</td>
                        <td>{{ agent.agent_telno }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-else class="result-value empty-list">No agents found</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- WordFit 弹窗组件 -->
    <WordFitWindow 
      ref="wordFitRef"
      :file-id="fileId"
      :extract-fields="extractFields"
      @extraction-complete="onExtractionComplete"
    />
  </div>
</template>

<style scoped>
.app-container {
  max-width: 1000px;
  margin: 0 auto;
  padding: 10px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

.app-header {
  margin-bottom: 15px;
  border-bottom: 1px solid #eee;
  padding-bottom: 5px;
}

.app-header h1 {
  font-size: 1.2rem;
  color: #333;
  margin: 0;
}

.app-main {
  display: flex;
  justify-content: center;
}

.content-wrapper {
  display: flex;
  flex-direction: column; /* 强制垂直布局 */
  gap: 15px;
  width: 100%;
}

.card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  padding: 15px;
  width: 100%;
}

/* 紧凑输入区域样式 */
.input-row {
  display: flex;
  align-items: center;
  gap: 15px;
  flex-wrap: wrap;
  margin-bottom: 15px;
}

.action-row {
  display: flex;
  gap: 15px;
  align-items: flex-start; /* 顶部对齐，防止高度变化影响 */
}

.new-component-wrapper {
  flex: 1; /* 让新组件占据剩余空间 */
}

.form-group {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 200px;
}

.form-group label {
  font-weight: 500;
  color: #374151;
  white-space: nowrap;
  font-size: 0.9rem;
}

.input-field {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 14px;
  box-sizing: border-box;
}

.primary-btn {
  background-color: #2563eb;
  color: white;
  padding: 6px 15px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-weight: 500;
  font-size: 14px;
  white-space: nowrap;
  transition: background-color 0.2s;
}

.primary-btn:hover {
  background-color: #1d4ed8;
}

.primary-btn.ghost {
  background-color: transparent;
  color: #2563eb;
  border: 1px solid #2563eb;
}

.primary-btn.ghost:hover {
  background-color: #eff6ff;
}

/* 结果展示样式 */
.result-card {
  border-top: 4px solid #10b981;
}

h2 {
  margin-top: 0;
  color: #444;
  margin-bottom: 15px;
  font-size: 1rem;
}

.result-section {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.result-row {
  width: 100%;
}

.result-row.two-col {
  display: flex;
  gap: 15px;
}

.result-row.two-col .result-item {
  flex: 1;
}

.result-item {
  /* margin-bottom handled by row gap */
}

.result-item label {
  display: block;
  font-size: 0.8rem;
  font-weight: 600;
  color: #6b7280;
  margin-bottom: 4px;
}

.result-value {
  font-size: 0.9rem;
  color: #111827;
  padding: 6px 10px;
  background-color: #f9fafb;
  border-radius: 4px;
  border: 1px solid #e5e7eb;
  min-height: 24px; /* 保证空值时也有高度 */
  word-break: break-word; /* 防止长文本溢出 */
}

.empty-list {
  color: #9ca3af;
  font-style: italic;
}

.table-container {
  overflow-x: auto;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
}

.agent-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.agent-table th,
.agent-table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid #e5e7eb;
}

.agent-table th {
  background-color: #f3f4f6;
  font-weight: 600;
  color: #374151;
}

.agent-table tr:last-child td {
  border-bottom: none;
}
</style>