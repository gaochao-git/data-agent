<template>
  <!-- 弹框 (仅在 isOpen 为 true 时渲染) -->
  <div v-if="isOpen" class="modal-overlay">
    <div class="modal-content">
      <div class="modal-header">
        <h2 class="modal-title">Extraction Progress</h2>
        <button @click="closeModal" class="close-btn">✕</button>
      </div>
      
      <div class="modal-body">
        <!-- 参数展示区域 -->
        <div class="params-box">
          <strong>File ID:</strong> {{ fileId }}
        </div>

        <!-- 节点列表 -->
        <div class="node-list">
          <div v-for="node in nodes" :key="node.nodeId" class="node-item">
            <div class="node-icon">
              <!-- Doing 图标 (Spinner) -->
              <svg v-if="node.status === 'doing'" class="spinner" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              
              <!-- Completed 图标 (Checkmark) -->
              <svg v-if="node.status === 'completed'" class="checkmark" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
              </svg>

              <!-- Pending 图标 -->
              <div v-if="node.status === 'pending'" class="pending-dot"></div>
            </div>
            
            <div class="node-content-wrapper">
              <div class="node-name">{{ node.nodeName }}</div>
              <div class="node-text">{{ node.content }}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="modal-footer">
        <button @click="closeModal" class="footer-close-btn">Close</button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'WordFitWindow',
  props: {
    fileId: {
      type: String,
      required: true
    },
    extractFields: {
      type: Array,
      required: true
    }
  },
  data() {
    return {
      isOpen: false,
      loading: false,
      nodes: [], // 存储节点状态: { nodeId, nodeName, status, content }
      llmContent: ''
    };
  },
  methods: {
    // 外部调用的启动方法
    start() {
      this.isOpen = true;
      this.handleExtract();
    },

    closeModal() {
      this.isOpen = false;
    },

    // 处理流式数据的每一行
    handleStreamLine(line) {
      if (!line.trim()) return;
      
      try {
        const data = JSON.parse(line);
        
        if (data.type === 'node') {
          const existingIndex = this.nodes.findIndex(n => n.nodeId === data.node);
          
          if (data.status === 'start') {
            if (existingIndex >= 0) {
              // 更新已存在的节点
              this.nodes[existingIndex].status = 'doing';
              this.nodes[existingIndex].content = data.content;
              this.nodes.splice(existingIndex, 1, this.nodes[existingIndex]);
            } else {
              // 添加新节点
              this.nodes.push({
                nodeId: data.node,
                nodeName: data.node_name,
                status: 'doing',
                content: data.content
              });
            }
          } else if (data.status === 'end') {
            if (existingIndex >= 0) {
              this.nodes[existingIndex].status = 'completed';
              this.nodes[existingIndex].content = data.content;
              this.nodes.splice(existingIndex, 1, this.nodes[existingIndex]);
            }
          }
        } else if (data.type === 'llm_chunk') {
           // 处理 LLM 实时输出
           this.llmContent += data.content;
        } else if (data.type === 'result') {
          // 回调结果
          this.handleResultCallback(data.data);
        }
      } catch (e) {
        console.error('Error parsing stream line:', e);
      }
    },

    // 结果回调函数
    handleResultCallback(data) {
      console.log('Result received:', data);
      this.$emit('extraction-complete', data);
      this.closeModal();
    },

    async handleExtract() {
      this.loading = true;
      this.nodes = [];
      this.llmContent = '';

      // 构建请求参数
      const requestBody = {
        file_id: this.fileId,
        extract_fields: this.extractFields
      };

      try {
        const response = await fetch('/api/word-fit/extract', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(requestBody),
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const chunk = decoder.decode(value, { stream: true });
          buffer += chunk;
          
          const lines = buffer.split('\n');
          // 保留最后一行（可能不完整），处理前面的完整行
          buffer = lines.pop() || '';
          
          for (const line of lines) {
            this.handleStreamLine(line);
          }
        }
        
        // 处理剩余的 buffer
        if (buffer) {
          this.handleStreamLine(buffer);
        }

      } catch (error) {
        console.error('Extraction failed:', error);
      } finally {
        this.loading = false;
      }
    }
  }
};
</script>

<style scoped>
/* 弹框遮罩 */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

/* 弹框内容 */
.modal-content {
  background-color: white;
  border-radius: 8px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
  width: 600px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
}

.modal-header {
  padding: 16px;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0;
}

.close-btn {
  background: none;
  border: none;
  font-size: 20px;
  color: #6b7280;
  cursor: pointer;
}

.modal-body {
  padding: 16px;
  overflow-y: auto;
  flex: 1;
}

.params-box {
  margin-bottom: 16px;
  padding: 8px;
  background-color: #f9fafb;
  border-radius: 4px;
  font-size: 14px;
  color: #4b5563;
}

/* 节点列表样式 */
.node-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.node-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px;
  border-radius: 4px;
  transition: background-color 0.2s;
}

.node-item:hover {
  background-color: #f9fafb;
}

.node-icon {
  flex-shrink: 0;
  margin-top: 4px;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.node-content-wrapper {
  flex: 1;
}

.node-name {
  font-weight: 500;
  color: #111827;
}

.node-text {
  font-size: 14px;
  color: #6b7280;
  margin-top: 4px;
}

/* 图标动画和样式 */
.spinner {
  animation: spin 1s linear infinite;
  color: #3b82f6; /* blue-500 */
  width: 20px;
  height: 20px;
}

.checkmark {
  color: #22c55e; /* green-500 */
  width: 20px;
  height: 20px;
}

.pending-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #d1d5db;
}

/* Tailwind Utilities */
.opacity-25 {
  opacity: 0.25;
}

.opacity-75 {
  opacity: 0.75;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.modal-footer {
  padding: 16px;
  border-top: 1px solid #e5e7eb;
  background-color: #f9fafb;
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
  display: flex;
  justify-content: flex-end;
}

.footer-close-btn {
  padding: 8px 16px;
  background-color: white;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  color: #374151;
  cursor: pointer;
}

.footer-close-btn:hover {
  background-color: #f3f4f6;
}
</style>