<template>
  <div class="wordfit-new-container">
    <!-- 初始状态：启动按钮 -->
    <button 
      v-if="!isRunning && !isFinished" 
      @click="start" 
      class="start-btn"
    >
      Start Extraction (Line Mode)
    </button>

    <!-- 运行/完成状态：单行信息展示 -->
    <div v-else class="status-bar" :class="{ 'finished': isFinished, 'error': isError }">
      <div class="status-content">
        <!-- Icon -->
        <div class="icon-wrapper">
           <svg v-if="!isFinished && !isError" class="spinner" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="#93c5fd" stroke-width="4"></circle>
              <path class="opacity-75" fill="#1e40af" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
           </svg>
           <svg v-else-if="isError" class="error-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
           </svg>
           <svg v-else class="checkmark" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
           </svg>
        </div>

        <!-- Text (Single Line) -->
        <span class="status-text" :title="fullStatusText">{{ displayStatusText }}</span>
      </div>
      
      <!-- Reset Action -->
      <button v-if="isFinished || isError" @click="reset" class="reset-btn" title="重置">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74-2.74L3 12" /><path d="M3 3v9h9" /></svg>
      </button>
    </div>
  </div>
</template>

<script>
export default {
  name: 'WordFitButton',
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
      isRunning: false,
      isFinished: false,
      isError: false,
      fullStatusText: '',
      displayStatusText: '初始化...',
      llmContent: ''
    };
  },
  methods: {
    start() {
      this.isRunning = true;
      this.isFinished = false;
      this.isError = false;
      this.updateStatus('初始化...');
      this.handleExtract();
    },

    reset() {
      this.isRunning = false;
      this.isFinished = false;
      this.isError = false;
      this.fullStatusText = '';
      this.displayStatusText = '';
    },

    updateStatus(text) {
      this.fullStatusText = text;
      this.displayStatusText = text;
    },

    // 处理流式数据的每一行
    handleStreamLine(line) {
      if (!line.trim()) return;
      
      try {
        const data = JSON.parse(line);
        
        if (data.type === 'node') {
          // data: { type: 'node', status: 'start'/'end', node_name: '...', content: '...' }
          const nodeName = data.node_name || 'Node';
          const nodeStart = data.content || `${nodeName} 处理中...`;
          const nodeEnd = data.content || `${nodeName} 处理完成`;  
          
          if (data.status === 'start') {
             this.updateStatus(`${nodeStart}`);
          } else if (data.status === 'end') {
             this.updateStatus(`${nodeEnd}`);
          } else if (data.content) {
             // 只有当有内容更新时，才覆盖显示内容摘要
             // 移除换行符，确保单行显示
             let cleanContent = data.content.replace(/[\r\n]+/g, ' ').trim();
             if (cleanContent.length > 40) {
               cleanContent = cleanContent.substring(0, 40) + '...';
             }
             if (cleanContent) {
               this.updateStatus(`${nodeName}: ${cleanContent}`);
             }
          }
        } else if (data.type === 'llm_chunk') {
           this.llmContent += data.content;
           // 可选：显示 LLM 生成动态
           // this.updateStatus(`Generating: ...`);
        } else if (data.type === 'result') {
          this.handleResultCallback(data.data);
        }
      } catch (e) {
        console.error('Error parsing stream line:', e);
      }
    },

    handleResultCallback(data) {
      console.log('Result received (WordFitButton):', data);
      this.isFinished = true;
      this.updateStatus('信息提取完成');
      this.$emit('extraction-complete', data);
    },

    async handleExtract() {
      this.llmContent = '';

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
          buffer = lines.pop() || '';
          
          for (const line of lines) {
            this.handleStreamLine(line);
          }
        }
        
        if (buffer) {
          this.handleStreamLine(buffer);
        }

      } catch (error) {
        console.error('Extraction failed:', error);
        this.isError = true;
        this.updateStatus(`Error: ${error.message}`);
      }
    }
  }
};
</script>

<style scoped>
.wordfit-new-container {
  display: inline-block;
  vertical-align: top;
  width: 100%;
}

.start-btn {
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
  width: 100%;
  height: 32px; /* 固定高度确保切换时平稳 */
}

.start-btn:hover {
  background-color: #1d4ed8;
}

.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  border: 1px solid #2563eb;
  background-color: #eff6ff; /* light blue bg */
  color: #1e40af;
  border-radius: 4px;
  width: 100%;
  height: 32px; /* 与按钮高度一致 */
  box-sizing: border-box;
  font-size: 13px;
}

.status-bar.finished {
  border-color: #10b981;
  background-color: #ecfdf5;
  color: #065f46;
}

.status-bar.error {
  border-color: #ef4444;
  background-color: #fef2f2;
  color: #991b1b;
}

.status-content {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
  flex: 1;
}

.icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.status-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
}

.reset-btn {
  background: none;
  border: none;
  padding: 2px;
  margin-left: 5px;
  cursor: pointer;
  color: inherit;
  opacity: 0.7;
  display: flex;
  align-items: center;
}

.reset-btn:hover {
  opacity: 1;
  background-color: rgba(0,0,0,0.05);
  border-radius: 4px;
}

/* Icons */
.spinner {
  animation: spin 1s linear infinite;
  width: 16px;
  height: 16px;
}

.checkmark {
  width: 16px;
  height: 16px;
}

.error-icon {
  width: 16px;
  height: 16px;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>