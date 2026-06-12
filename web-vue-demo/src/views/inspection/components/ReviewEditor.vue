<template>
  <div :class="`flex flex-col bg-white dark:bg-gray-900 ${panelHeightClass}`">
    <!-- 头部 -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-amber-50 dark:bg-amber-900/20">
      <div class="flex items-center gap-2">
        <div class="flex h-6 w-6 items-center justify-center rounded-full bg-amber-500 text-white">
          <i :class="`fa-solid ${config.icon} text-xs`"></i>
        </div>
        <h3 class="text-sm font-medium text-gray-900 dark:text-white">
          {{ config.title }}
        </h3>
        <span class="text-xs text-amber-600 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/40 px-2 py-0.5 rounded">
          待确认
        </span>
      </div>
    </div>

    <!-- 内容区域 - 可滚动 -->
    <div class="flex-1 overflow-y-auto overflow-x-hidden p-4">
      <div class="space-y-4 pb-4">
        <!-- ============ Extract 类型 ============ -->
        <template v-if="reviewType === 'extract'">
          <div class="space-y-4">
            <!-- 多文件模式 -->
            <template v-if="'files' in editedExtractData">
              <div class="space-y-3">
                <div
                  v-for="file in editedExtractData.files"
                  :key="file.file_index"
                  class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
                >
                  <!-- 文件头部 -->
                  <div
                    class="px-4 py-3 bg-gray-50 dark:bg-gray-800/50 flex items-center justify-between cursor-pointer"
                    @click="toggleFileExpand(file.file_index)"
                  >
                    <div class="flex items-center gap-3">
                      <span class="w-6 h-6 rounded-full bg-indigo-600 text-white text-xs flex items-center justify-center font-medium">
                        {{ file.file_index }}
                      </span>
                      <span class="text-sm font-medium text-gray-900 dark:text-white">
                        事实认定书 {{ file.file_index }}
                      </span>
                      <span class="text-xs text-gray-500 dark:text-gray-400 truncate max-w-[300px]">
                        {{ file.inspection_content?.substring(0, 30) || '未填写检查内容' }}...
                      </span>
                    </div>
                    <i :class="mergeClassNames(
                      'fa-solid fa-chevron-down text-gray-400 transition-transform',
                      expandedFileIndexes.has(file.file_index) ? 'rotate-180' : ''
                    )"></i>
                  </div>

                  <!-- 文件详情 -->
                  <div
                    v-show="expandedFileIndexes.has(file.file_index)"
                    class="overflow-hidden"
                  >
                    <div class="px-4 py-4 space-y-4 border-t border-gray-200 dark:border-gray-700">
                      <div>
                        <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                          被检查人
                        </label>
                        <input
                          type="text"
                          v-model="file.inspected_person"
                          @input="handleMultiFileFieldChange(file.file_index, 'inspected_person', ($event.target as HTMLInputElement).value)"
                          class="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                          placeholder="被检查人"
                        />
                      </div>
                      <div>
                        <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                          检查时间
                        </label>
                        <input
                          type="text"
                          v-model="file.inspection_time"
                          @input="handleMultiFileFieldChange(file.file_index, 'inspection_time', ($event.target as HTMLInputElement).value)"
                          class="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                          placeholder="检查时间"
                        />
                      </div>
                      <div>
                        <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                          检查内容
                        </label>
                        <input
                          type="text"
                          v-model="file.inspection_content"
                          @input="handleMultiFileFieldChange(file.file_index, 'inspection_content', ($event.target as HTMLInputElement).value)"
                          class="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                          placeholder="检查内容"
                        />
                      </div>
                      <div>
                        <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                          违法事实描述
                        </label>
                        <textarea
                          v-model="file.violation_facts"
                          @input="handleMultiFileFieldChange(file.file_index, 'violation_facts', ($event.target as HTMLTextAreaElement).value)"
                          class="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                          placeholder="违法事实描述"
                          rows="12"
                        />
                      </div>
                    </div>
                  </div>
                </div>
                <div class="text-xs text-gray-500 dark:text-gray-400 text-right">
                  共 {{ editedExtractData.files.length }} 份事实认定书
                </div>
              </div>
            </template>

            <!-- 单文件模式 -->
            <template v-else>
              <div class="space-y-4">
                <div>
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    检查内容
                  </label>
                  <input
                    type="text"
                    v-model="editedExtractData.inspection_content"
                    @input="handleExtractFieldChange('inspection_content', ($event.target as HTMLInputElement).value)"
                    class="w-full px-3 py-2.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                    placeholder="检查内容"
                  />
                </div>
                <div>
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    违法事实描述
                  </label>
                  <textarea
                    v-model="editedExtractData.violation_facts"
                    @input="handleExtractFieldChange('violation_facts', ($event.target as HTMLTextAreaElement).value)"
                    class="w-full px-3 py-2.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent resize-none"
                    placeholder="违法事实描述"
                    rows="12"
                  />
                </div>
              </div>
            </template>
          </div>
        </template>

        <!-- ============ Evidence 类型 ============ -->
        <template v-if="reviewType === 'evidence'">
          <div class="space-y-4">
            <div class="overflow-x-auto">
              <table class="w-full border-collapse text-sm min-w-[800px]">
                <thead>
                  <tr class="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-xs">
                    <th class="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-center w-14">序号</th>
                    <th class="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left min-w-[180px]">证据名称及编号</th>
                    <th class="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left min-w-[200px]">证据来源</th>
                    <th class="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left w-32">表现形式</th>
                    <th class="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left min-w-[200px]">真实性说明</th>
                    <th class="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-center w-14">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(evidence, index) in editedEvidenceData.evidence_list"
                    :key="index"
                    class="bg-white dark:bg-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800"
                  >
                    <td class="border border-gray-200 dark:border-gray-700 px-3 py-2 text-center text-gray-700 dark:text-gray-300 font-medium">
                      {{ index + 1 }}
                    </td>
                    <td class="border border-gray-200 dark:border-gray-700 p-1.5">
                      <input
                        type="text"
                        v-model="evidence.evidence_name_code"
                        @input="handleEvidenceFieldChange(index, 'evidence_name_code', ($event.target as HTMLInputElement).value)"
                        class="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                        placeholder="如：3-4-1-1(1)：XX表"
                      />
                    </td>
                    <td class="border border-gray-200 dark:border-gray-700 p-1.5">
                      <textarea
                        v-model="evidence.evidence_source"
                        @input="handleEvidenceFieldChange(index, 'evidence_source', ($event.target as HTMLTextAreaElement).value)"
                        class="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                        rows="2"
                        placeholder="证据来源"
                      />
                    </td>
                    <td class="border border-gray-200 dark:border-gray-700 p-1.5">
                      <input
                        type="text"
                        v-model="evidence.evidence_form"
                        @input="handleEvidenceFieldChange(index, 'evidence_form', ($event.target as HTMLInputElement).value)"
                        class="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                        placeholder="电子证据"
                      />
                    </td>
                    <td class="border border-gray-200 dark:border-gray-700 p-1.5">
                      <textarea
                        v-model="evidence.evidence_truth"
                        @input="handleEvidenceFieldChange(index, 'evidence_truth', ($event.target as HTMLTextAreaElement).value)"
                        class="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                        rows="2"
                        placeholder="真实性说明"
                      />
                    </td>
                    <td class="border border-gray-200 dark:border-gray-700 px-3 py-2 text-center">
                      <button
                        type="button"
                        @click="handleDeleteEvidence(index)"
                        class="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                        title="删除此证据"
                      >
                        <i class="fa-solid fa-trash-can"></i>
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <button
              type="button"
              @click="handleAddEvidence"
              class="w-full py-2.5 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-2 text-sm"
            >
              <i class="fa-solid fa-plus"></i>
              <span>添加证据</span>
            </button>
            <div class="text-xs text-gray-500 dark:text-gray-400 text-right">
              共 {{ editedEvidenceData.evidence_list.length }} 条证据记录
            </div>
          </div>
        </template>

        <!-- ============ Violations 类型 (split/analyze/retrieve) ============ -->
        <template v-if="reviewType !== 'extract' && reviewType !== 'evidence'">
          <div class="space-y-4">
            <!-- 案件基本信息（仅检索审核时显示，全局显示一次） -->
            <template v-if="showFullFields && editedViolations.length > 0">
              <div class="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg space-y-3">
                <div class="flex items-center gap-2 text-sm font-semibold text-blue-900 dark:text-blue-300 mb-3">
                  <i class="fa-solid fa-info-circle"></i>
                  <span>案件基本信息</span>
                </div>
                <div class="grid grid-cols-2 gap-3">
                  <div>
                    <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                      被检查人
                    </label>
                    <input
                      type="text"
                      :value="editedViolations[0]?.inspected_person || ''"
                      @input="{
                        isEditing = true;
                        editedViolations = editedViolations.map(v => ({ ...v, inspected_person: ($event.target as HTMLInputElement).value }));
                      }"
                      class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                      placeholder="被检查人名称"
                    />
                  </div>
                  <div>
                    <label class="block text-xs font-medium-medium text-gray-700 dark:text-gray-300 mb-1">
                      检查时间
                    </label>
                    <input
                      type="text"
                      :value="editedViolations[0]?.inspection_date || ''"
                      @input="{
                        isEditing = true;
                        editedViolations = editedViolations.map(v => ({ ...v, inspection_date: ($event.target as HTMLInputElement).value }));
                      }"
                      class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                      placeholder="检查时间"
                    />
                  </div>
                </div>
                <div class="text-xs text-blue-600 dark:text-blue-400 flex items-start gap-2 mt-2">
                  <i class="fa-solid fa-lightbulb mt-0.5"></i>
                  <span>提示：基本信息为全局信息，将应用于整个案件的所有检查内容</span>
                </div>
              </div>
            </template>

            <!-- 违法行为列表 -->
            <div class="space-y-3">
              <div
                v-for="(violation, index) in editedViolations"
                :key="violation.violation_id"
                class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
              >
                <!-- 违法行为头部 -->
                <div
                  class="px-4 py-3 bg-gray-50 dark:bg-gray-800/50 flex items-center justify-between cursor-pointer"
                  @click="toggleViolationExpand(violation.violation_id)"
                >
                  <div class="flex items-center gap-3">
                    <span class="w-6 h-6 rounded-full bg-indigo-600 text-white text-xs flex items-center justify-center font-medium">
                      {{ violation.violation_id }}
                    </span>
                    <span class="text-sm font-medium text-gray-900 dark:text-white">
                      检查内容 {{ violation.violation_id }}
                    </span>
                    <span class="text-xs text-gray-500 dark:text-gray-400 truncate max-w-[300px]">
                      {{ violation.title || violation.facts }}
                    </span>
                  </div>
                  <div class="flex items-center gap-2">
                    <!-- 附件按钮 - 显示文件名 -->
                    <template v-if="violation.source_files && violation.source_files.length > 0">
                      <div class="flex items-center gap-1">
                        <button
                          v-for="(file, fileIdx) in violation.source_files"
                          :key="fileIdx"
                          type="button"
                          @click.stop="$emit('view-attachment', file)"
                          class="px-2 py-1 text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded transition-colors flex items-center gap-1 border border-blue-200 dark:border-blue-800"
                          :title="`查看 ${file.file_type || file.filename || file.file_name || '附件'}`"
                        >
                          <i class="fa-solid fa-file-word text-blue-500"></i>
                          <span class="max-w-[120px] truncate">
                            {{ file.file_type || file.filename || file.file_name || `附件${(fileIdx as number) + 1}` }}
                          </span>
                        </button>
                      </div>
                    </template>

                    <button
                      type="button"
                      @click.stop="handleDeleteViolation(index)"
                      class="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                    >
                      <i class="fa-solid fa-trash-can text-xs"></i>
                    </button>
                    <i :class="mergeClassNames(
                      'fa-solid fa-chevron-down text-gray-400 transition-transform',
                      expandedViolationIds.has(violation.violation_id) ? 'rotate-180' : ''
                    )"></i>
                  </div>
                </div>

                <!-- 违法行为详情 -->
                <div
                  v-show="expandedViolationIds.has(violation.violation_id)"
                  class="overflow-hidden"
                >
                  <div class="px-4 py-4 space-y-4 border-t border-gray-200 dark:border-gray-700">
                    <!-- 原始事实认定 -->
                    <div>
                      <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                        原始事实认定
                      </label>
                      <textarea
                        :value="violation.facts"
                        @input="handleViolationFieldChange(index as number, 'facts', ($event.target as HTMLTextAreaElement).value)"
                        class="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                        rows="8"
                      />
                    </div>

                    <!-- 取证记录（仅证据目录报告显示） -->
                    <template v-if="reviewData.requires_evidence_upload">
                      <div class="border border-dashed border-gray-300 dark:border-gray-600 rounded-lg p-3">
                        <div class="mb-3">
                          <label class="text-xs font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1.5 mb-3">
                            <i class="fa-solid fa-clipboard-list text-gray-500"></i>
                            取证记录
                            <span class="text-red-500 text-xs">*必传</span>
                          </label>

                          <!-- 上传状态提示 -->
                          <div :class="`mb-2 px-2 py-1 rounded text-xs ${
                            violationUploadStatus[violation.violation_id]
                              ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400'
                              : 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400'
                          }`">
                            <template v-if="violationUploadStatus[violation.violation_id]">
                              <i class="fa-solid fa-check-circle mr-1"></i>已上传取证记录表
                            </template>
                            <template v-else>
                              <i class="fa-solid fa-exclamation-triangle mr-1"></i>请上传取证记录表
                            </template>
                          </div>

                          <template v-if="!violation.evidence_list || violation.evidence_list.length === 0">
                            <div class="flex justify-center py-4">
                              <input
                                ref="fileInputRef"
                                type="file"
                                accept=".docx,.wps"
                                class="hidden"
                                @change="(e: Event) => {
                                  const target = e.target as HTMLInputElement;
                                  const file = target.files?.[0];
                                  if (file) {
                                    handleEvidenceFileUpload(index, file);
                                    target.value = '';
                                  }
                                }"
                              />
                              <button
                                type="button"
                                @click="(e: MouseEvent) => {
                                  const el = e.currentTarget as HTMLElement;
                                  const input = el.previousElementSibling as HTMLInputElement;
                                  input.click();
                                }"
                                class="px-3 py-2 text-sm bg-indigo-500 text-white rounded hover:bg-indigo-600 transition-colors flex items-center gap-2"
                              >
                                <i class="fa-solid fa-upload"></i>
                                上传取证记录表
                              </button>
                            </div>
                          </template>
                        </div>

                        <!-- 证据列表表格 -->
                        <template v-if="violation.evidence_list && violation.evidence_list.length > 0">
                          <div class="space-y-2">
                            <label class="block text-xs text-gray-600 dark:text-gray-400">证据清单</label>
                            <div class="overflow-x-auto">
                              <table class="w-full border-collapse text-xs min-w-[700px]">
                                <thead>
                                  <tr class="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                                    <th class="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center w-10">序号</th>
                                    <th class="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left min-w-[140px]">证据名称及编号</th>
                                    <th class="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left min-w-[150px]">证据来源</th>
                                    <th class="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left w-24">表现形式</th>
                                    <th class="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left min-w-[150px]">真实性说明</th>
                                    <th class="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center w-12">操作</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  <tr
                                    v-for="(evidence, evidenceIdx) in violation.evidence_list"
                                    :key="evidenceIdx"
                                    class="bg-white dark:bg-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800"
                                  >
                                    <td class="border border-gray-200 dark:border-gray-700 px-2 py-1 text-center text-gray-700 dark:text-gray-300 font-medium">
                                      {{ (evidenceIdx as number) + 1 }}
                                    </td>
                                    <td class="border border-gray-200 dark:border-gray-700 p-1">
                                      <input
                                        type="text"
                                        :value="evidence.evidence_name_code || ''"
                                        @input="handleViolationEvidenceFieldChange(index as number, evidenceIdx as number, 'evidence_name_code', ($event.target as HTMLInputElement).value)"
                                        class="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                        placeholder="如：3-4-1-1(1)：XX表"
                                      />
                                    </td>
                                    <td class="border border-gray-200 dark:border-gray-700 p-1">
                                      <textarea
                                        :value="evidence.evidence_source || ''"
                                        @input="handleViolationEvidenceFieldChange(index as number, evidenceIdx as number, 'evidence_source', ($event.target as HTMLTextAreaElement).value)"
                                        class="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                        rows="1"
                                        placeholder="证据来源"
                                      />
                                    </td>
                                    <td class="border border-gray-200 dark:border-gray-700 p-1">
                                      <input
                                        type="text"
                                        :value="evidence.evidence_form || ''"
                                        @input="handleViolationEvidenceFieldChange(index as number, evidenceIdx as number, 'evidence_form', ($event.target as HTMLInputElement).value)"
                                        class="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                        placeholder="电子证据"
                                      />
                                    </td>
                                    <td class="border border-gray-200 dark:border-gray-700 p-1">
                                      <textarea
                                        :value="evidence.evidence_truth || ''"
                                        @input="handleViolationEvidenceFieldChange(index as number, evidenceIdx as number, 'evidence_truth', ($event.target as HTMLTextAreaElement).value)"
                                        class="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                        rows="1"
                                        placeholder="真实性说明"
                                      />
                                    </td>
                                    <td class="border border-gray-200 dark:border-gray-700 px-2 py-1 text-center">
                                      <button
                                        type="button"
                                        @click="handleDeleteViolationEvidence(index as number, evidenceIdx as number)"
                                        class="p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                                        title="删除此证据"
                                      >
                                        <i class="fa-solid fa-trash-can text-xs"></i>
                                      </button>
                                    </td>
                                  </tr>
                                </tbody>
                              </table>
                            </div>
                            <button
                              type="button"
                              @click="handleAddViolationEvidence(index as number)"
                              class="w-full py-1.5 border border-dashed border-gray-300 dark:border-gray-600 rounded text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1"
                            >
                              <i class="fa-solid fa-plus"></i>
                              添加证据
                            </button>
                            <div class="text-xs text-gray-500 dark:text-gray-400 text-right">
                              共 {{ violation.evidence_list.length }} 条证据记录
                            </div>
                          </div>
                        </template>
                      </div>
                    </template>

                    <!-- 检索结果（retrieve 类型显示完整字段） -->
                    <template v-if="showFullFields">
                      <!-- 违反条款 -->
                      <div>
                        <label class="block text-sm font-semibold text-gray-900 dark:text-white mb-2">违反条款</label>
                        <div class="space-y-2">
                          <template v-for="(law, i) in (violation.legal_basis || [])" :key="i">
                            <div class="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg space-y-2">
                              <div class="flex items-center gap-2">
                                <input
                                  type="text"
                                  :value="law.law_name"
                                  @input="handleLawItemChange(index as number, 'legal_basis', i as number, 'law_name', ($event.target as HTMLInputElement).value)"
                                  placeholder="法律名称"
                                  class="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                />
                                <input
                                  type="text"
                                  :value="law.law_item_name"
                                  @input="handleLawItemChange(index as number, 'legal_basis', i as number, 'law_item_name', ($event.target as HTMLInputElement).value)"
                                  placeholder="条款编号"
                                  class="w-24 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                />
                                <button
                                  type="button"
                                  @click="$emit('view-law', law.law_name, law.law_item_name)"
                                  class="p-1.5 text-indigo-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 rounded transition-colors"
                                  title="查看原文"
                                >
                                  <i class="fa-solid fa-book-open text-xs"></i>
                                </button>
                                <button
                                  type="button"
                                  @click="handleDeleteLawItem(index as number, 'legal_basis', i as number)"
                                  class="p-1.5 text-gray-400 hover:text-red-500 transition-colors"
                                >
                                  <i class="fa-solid fa-trash-can text-xs"></i>
                                </button>
                              </div>
                              <textarea
                                :value="law.law_item_content"
                                @input="handleLawItemChange(index as number, 'legal_basis', i as number, 'law_item_content', ($event.target as HTMLTextAreaElement).value)"
                                placeholder="条款内容"
                                class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                rows="2"
                              />
                            </div>
                          </template>
                          <template v-if="!violation.legal_basis || violation.legal_basis.length === 0">
                            <div class="text-xs text-gray-400 text-center py-2">暂无</div>
                          </template>
                          <button
                            type="button"
                            @click="handleAddLawItem(index as number, 'legal_basis')"
                            class="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1"
                          >
                            <i class="fa-solid fa-plus"></i>添加违反条款
                          </button>
                        </div>
                      </div>

                      <!-- 处罚条款 -->
                      <div>
                        <label class="block text-sm font-semibold text-gray-900 dark:text-white mb-2">处罚条款</label>
                        <div class="space-y-2">
                          <template v-for="(law, i) in (violation.penalty_clause || [])" :key="i">
                            <div class="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg space-y-2">
                              <div class="flex items-center gap-2">
                                <input
                                  type="text"
                                  :value="law.law_name"
                                  @input="handleLawItemChange(index as number, 'penalty_clause', i as number, 'law_name', ($event.target as HTMLInputElement).value)"
                                  placeholder="法律名称"
                                  class="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                />
                                <input
                                  type="text"
                                  :value="law.law_item_name"
                                  @input="handleLawItemChange(index as number, 'penalty_clause', i as number, 'law_item_name', ($event.target as HTMLInputElement).value)"
                                  placeholder="条款编号"
                                  class="w-24 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                />
                                <button
                                  type="button"
                                  @click="$emit('view-law', law.law_name, law.law_item_name)"
                                  class="p-1.5 text-indigo-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 rounded transition-colors"
                                  title="查看原文"
                                >
                                  <i class="fa-solid fa-book-open text-xs"></i>
                                </button>
                                <button
                                  type="button"
                                  @click="handleDeleteLawItem(index, 'penalty_clause', i)"
                                  class="p-1.5 text-gray-400 hover:text-red-500 transition-colors"
                                >
                                  <i class="fa-solid fa-trash-can text-xs"></i>
                                </button>
                              </div>
                              <textarea
                                :value="law.law_item_content"
                                @input="handleLawItemChange(index as number, 'penalty_clause', i as number, 'law_item_content', ($event.target as HTMLTextAreaElement).value)"
                                placeholder="条款内容"
                                class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                rows="2"
                              />
                            </div>
                          </template>
                          <template v-if="!violation.penalty_clause || violation.penalty_clause.length === 0">
                            <div class="text-xs text-gray-400 text-center py-2">暂无</div>
                          </template>
                          <button
                            type="button"
                            @click="handleAddLawItem(index as number, 'penalty_clause')"
                            class="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1"
                                                   >
                            <i class="fa-solid fa-plus"></i>添加处罚条款
                          </button>
                        </div>
                      </div>

                      <!-- 裁量基准 -->
                      <div>
                        <label class="block text-sm font-semibold text-gray-900 dark:text-white mb-2">裁量基准</label>
                        <div class="space-y-2">
                          <template v-for="(item, i) in (violation.discretion_standard || [])" :key="i">
                            <div class="flex items-start gap-2">
                              <span class="text-gray-400 text-xs mt-2">•</span>
                              <textarea
                                :value="item"
                                @input="handleStringArrayItemChange(index as number, 'discretion_standard', i as number, ($event.target as HTMLTextAreaElement).value)"
                                class="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                rows="1"
                              />
                              <button
                                type="button"
                                @click="handleDeleteStringArrayItem(index as number, 'discretion_standard', i as number)"
                                class="p-1.5 text-gray-400 hover:text-red-500 transition-colors"
                              >
                                <i class="fa-solid fa-trash-can text-xs"></i>
                              </button>
                            </div>
                          </template>
                          <template v-if="!violation.discretion_standard || violation.discretion_standard.length === 0">
                            <div class="text-xs text-gray-400 text-center py-2">暂无</div>
                          </template>
                          <button
                            type="button"
                            @click="handleAddStringArrayItem(index as number, 'discretion_standard')"
                            class="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1"
                          >
                            <i class="fa-solid fa-plus"></i>添加裁量基准
                          </button>
                        </div>
                      </div>

                      <!-- 整改措施 -->
                      <div>
                        <label class="block text-sm font-semibold text-gray-900 dark:text-white mb-2">整改措施</label>
                        <div class="space-y-2">
                          <template v-for="(item, i) in (violation.rectification_measures || [])" :key="i">
                            <div class="flex items-start gap-2">
                              <span class="text-gray-400 text-xs mt-2">•</span>
                              <textarea
                                :value="item"
                                @input="handleStringArrayItemChange(index as number, 'rectification_measures', i as number, ($event.target as HTMLTextAreaElement).value)"
                                class="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                rows="1"
                              />
                              <button
                                type="button"
                                @click="handleDeleteStringArrayItem(index as number, 'rectification_measures', i as number)"
                                class="p-1.5 text-gray-400 hover:text-red-500 transition-colors"
                              >
                                <i class="fa-solid fa-trash-can text-xs"></i>
                              </button>
                            </div>
                          </template>
                          <template v-if="!violation.rectification_measures || violation.rectification_measures.length === 0">
                            <div class="text-xs text-gray-400 text-center py-2">暂无</div>
                          </template>
                          <button
                            type="button"
                            @click="handleAddStringArrayItem(index as number, 'rectification_measures')"
                            class="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1"
                          >
                            <i class="fa-solid fa-plus"></i>添加整改措施
                          </button>
                        </div>
                      </div>

                      <!-- 处罚建议 -->
                      <div>
                        <label class="block text-sm font-semibold text-gray-900 dark:text-white mb-2">处罚建议</label>
                        <div class="space-y-2">
                          <template v-for="(item, i) in (violation.penalty_suggestion || [])" :key="i">
                            <div class="flex items-start gap-2">
                              <span class="text-gray-400 text-xs mt-2">•</span>
                              <textarea
                                :value="item"
                                @input="handleStringArrayItemChange(index as number, 'penalty_suggestion', i as number, ($event.target as HTMLTextAreaElement).value)"
                                class="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500"
                                rows="1"
                              />
                              <button
                                type="button"
                                @click="handleDeleteStringArrayItem(index as number, 'penalty_suggestion', i as number)"
                                class="p-1.5 text-gray-400 hover:text-red-500 transition-colors"
                              >
                                <i class="fa-solid fa-trash-can text-xs"></i>
                              </button>
                            </div>
                          </template>
                          <template v-if="!violation.penalty_suggestion || violation.penalty_suggestion.length === 0">
                            <div class="text-xs text-gray-400 text-center py-2">暂无</div>
                          </template>
                          <button
                            type="button"
                            @click="handleAddStringArrayItem(index as number, 'penalty_suggestion')"
                            class="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1"
                          >
                            <i class="fa-solid fa-plus"></i>添加
处罚建议
                          </button>
                        </div>
                      </div>
                    </template>
                  </div>
                </div>
              </div>
            </div>

            <!-- 添加检查内容按钮（仅拆分审核和分析审核时显示） -->
            <template v-if="reviewType === 'split' || reviewType === 'analyze'">
              <button
                type="button"
                @click="handleAddViolation"
                class="w-full py-2.5 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-2 text-sm"
              >
                <i class="fa-solid fa-plus"></i>
                <span>添加检查内容</span>
              </button>
            </template>

            <div class="text-xs text-gray-500 dark:text-gray-400 text-right">
              共 {{ editedViolations.length }} 条检查内容
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 底部操作栏 -->
    <div class="flex items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
      <div class="text-xs text-gray-500 dark:text-gray-400">
        <span v-if="isEditing" class="flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
          <i class="fa-solid fa-pen"></i>
          已修改
        </span>
        <!-- 调试信息：显示上传进度 -->
        <div v-if="reviewData?.requires_evidence_upload && !reviewData?.all_evidence_uploaded" class="mt-1">
          <span :class="Object.values(violationUploadStatus).every(Boolean) ? 'text-green-600' : 'text-amber-600'">
            已上传: {{ Object.values(violationUploadStatus).filter(Boolean).length }}/{{ editedViolations.length }} 个取证记录表
          </span>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button
          type="button"
          @click="handleCancel"
          class="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors"
        >
          取消任务
        </button>
        <button
          type="button"
          @click="handleConfirm"
          :disabled="isConfirmDisabled"
          :class="`px-5 py-2 text-sm font-medium text-white rounded-lg transition-colors flex items-center gap-2 ${
            isConfirmDisabled
              ? 'bg-gray-400 dark:bg-gray-600 cursor-not-allowed'
              : 'bg-indigo-600 hover:bg-indigo-700'
          }`"
          :title="confirmDisabledTitle"
        >
          <i class="fa-solid fa-check"></i>
          {{ isEditing ? '确认修改' : '确认继续' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { EvidenceItem, HumanReviewData } from '../types'

// 使用 /agents 前缀代理到 AI Agent 后端 (5001端口)
const API_BASE = (import.meta as any).env?.VITE_APP_BASE_API || ''

// 审核数据类型
type ReviewEditData =
  | { violations: any[] }
  | { inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string }
  | { files: Array<{ file_index: number; inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string }> }
  | { evidence_list: EvidenceItem[]; basic_info: Record<string, string> }

interface Props {
  reviewData?: HumanReviewData
  onConfirm?: (data: ReviewEditData, isEdited: boolean) => void
  onCancel?: () => void
  onUploadEvidence?: (file: File, violationId: number) => Promise<void>
  threadId?: string
  panelHeightClass?: string
}

const props = withDefaults(defineProps<Props>(), {
  reviewData: () => ({
    review_type: 'extract',
    message: '',
    current_data: {}
  }),
  threadId: '',
  panelHeightClass: 'h-full'
})

const emit = defineEmits<{
  (e: 'confirm', data: ReviewEditData, isEdited: boolean): void
  (e: 'cancel'): void
  (e: 'view-law', lawName: string, lawItemName: string): void
  (e: 'view-attachment', file: any): void
}>()

// 审核类型配置
const REVIEW_TYPE_CONFIG: Record<string, { title: string; icon: string }> = {
  extract: { title: '确认解析结果', icon: 'fa-file-lines' },
  split: { title: '确认拆分结果', icon: 'fa-scissors' },
  analyze: { title: '确认分析结果', icon: 'fa-magnifying-glass-chart' },
  retrieve: { title: '确认检索结果', icon: 'fa-scale-balanced' },
  evidence: { title: '确认取证记录', icon: 'fa-clipboard-list' },
}

const reviewType = computed(() => props.reviewData.review_type || 'extract')
const config = computed(() => REVIEW_TYPE_CONFIG[reviewType.value] || { title: '审核确认', icon: 'fa-check-circle' })

// Only retrieve type shows full fields
const showFullFields = computed(() => reviewType.value === 'retrieve')

// 本地编辑状态
const isEditing = ref(false)

// 本地跟踪每个违法行为的取证记录表上传状态（用于证据目录报告）
const violationUploadStatus = ref<Record<number, boolean>>(props.reviewData?.violation_upload_status || {})

// extract 类型的编辑状态
const editedExtractData = ref<
  { inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string } |
  { files: Array<{ file_index: number; inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string }> }
>({
  inspection_content: '',
  violation_facts: '',
})

// 当前展开的文件索引集合
const expandedFileIndexes = ref<Set<number>>(new Set([1]))

// violations 类型的编辑状态
const editedViolations = ref<any[]>([])
const expandedViolationIds = ref<Set<number>>(new Set())

// evidence 类型的编辑状态
const editedEvidenceData = ref<{
  evidence_list: EvidenceItem[]
  basic_info: Record<string, string>
}>({ evidence_list: [], basic_info: {} })

// 初始化编辑数据
watch(() => props.reviewData, (newReviewData, oldReviewData) => {
  isEditing.value = false

  const reviewType = newReviewData.review_type

  // 更新取证记录表上传状态
  // 保留本地上传状态，合并新数据（避免覆盖已上传的状态）
  const serverStatus = newReviewData.violation_upload_status || {}
  violationUploadStatus.value = { ...serverStatus, ...violationUploadStatus.value }

  // 默认展开第一个违法行为
  if (newReviewData.current_data?.violations?.length > 0) {
    expandedViolationIds.value = new Set([newReviewData.current_data.violations[0].violation_id])
  } else {
    expandedViolationIds.value = new Set()
  }

  if (reviewType === 'extract') {
    if (newReviewData.current_data?.files) {
      editedExtractData.value = {
        files: JSON.parse(JSON.stringify(newReviewData.current_data.files))
      }
      expandedFileIndexes.value = new Set([newReviewData.current_data.files[0]?.file_index || 1])
    } else {
      editedExtractData.value = {
        inspection_content: newReviewData.current_data?.inspection_content || '',
        violation_facts: newReviewData.current_data?.violation_facts || '',
      }
      expandedFileIndexes.value = new Set()
    }
  } else if (reviewType === 'evidence') {
    editedEvidenceData.value = {
      evidence_list: JSON.parse(JSON.stringify(newReviewData.current_data?.evidence_list || [])),
      basic_info: { ...(newReviewData.current_data?.basic_info || {}) },
    }
  } else if (newReviewData.current_data?.violations) {
    editedViolations.value = JSON.parse(JSON.stringify(newReviewData.current_data.violations))
  }
}, { immediate: true })

// 工具函数
function mergeClassNames(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(' ')
}

function toggleFileExpand(fileIndex: number) {
  const newSet = new Set(expandedFileIndexes.value)
  if (newSet.has(fileIndex)) {
    newSet.delete(fileIndex)
  } else {
    newSet.add(fileIndex)
  }
  expandedFileIndexes.value = newSet
}

// 处理函数
function handleExtractFieldChange(field: 'inspection_content' | 'violation_facts', value: string) {
  isEditing.value = true
  if (!('files' in editedExtractData.value)) {
    editedExtractData.value = { ...editedExtractData.value, [field]: value }
  }
}

function handleMultiFileFieldChange(
  fileIndex: number,
  field: 'inspection_content' | 'violation_facts' | 'inspected_person' | 'inspection_time',
  value: string
) {
  isEditing.value = true
  if ('files' in editedExtractData.value) {
    const updated = { ...editedExtractData.value }
    updated.files = editedExtractData.value.files.map(file =>
      file.file_index === fileIndex ? { ...file, [field]: value } : file
    )
    editedExtractData.value = updated
  }
}

// Violations 类型处理函数
function handleViolationFieldChange(index: number, field: string, value: any) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  updated[index] = { ...updated[index], [field]: value }
  editedViolations.value = updated
}

function handleDeleteViolation(index: number) {
  isEditing.value = true
  const updated = editedViolations.value.filter((_, i) => i !== index)
  editedViolations.value = updated.map((v, i) => ({ ...v, violation_id: i + 1 }))
}

function handleAddViolation() {
  isEditing.value = true
  editedViolations.value = [
    ...editedViolations.value,
    { violation_id: editedViolations.value.length + 1, title: '', facts: '' }
  ]
}

function handleLawItemChange(
  violationIndex: number,
  field: 'legal_basis' | 'penalty_clause',
  lawIndex: number,
  lawField: 'law_name' | 'law_item_name' | 'law_item_content',
  value: string
) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const lawItems = [...(updated[violationIndex][field] || [])]
  lawItems[lawIndex] = { ...lawItems[lawIndex], [lawField]: value }
  updated[violationIndex] = { ...updated[violationIndex], [field]: lawItems }
  editedViolations.value = updated
}

function handleDeleteLawItem(
  violationIndex: number,
  field: 'legal_basis' | 'penalty_clause',
  lawIndex: number
) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const lawItems = [...(updated[violationIndex][field] || [])]
  lawItems.splice(lawIndex, 1)
  updated[violationIndex] = { ...updated[violationIndex], [field]: lawItems }
  editedViolations.value = updated
}

function handleAddLawItem(violationIndex: number, field: 'legal_basis' | 'penalty_clause') {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const lawItems = [...(updated[violationIndex][field] || [])]
  lawItems.push({ law_name: '', law_item_name: '', law_item_content: '' })
  updated[violationIndex] = { ...updated[violationIndex], [field]: lawItems }
  editedViolations.value = updated
}

function handleStringArrayItemChange(
  violationIndex: number,
  field: 'discretion_standard' | 'rectification_measures' | 'penalty_suggestion',
  itemIndex: number,
  value: string
) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const items = [...(updated[violationIndex][field] || [])]
  items[itemIndex] = value
  updated[violationIndex] = { ...updated[violationIndex], [field]: items }
  editedViolations.value = updated
}

function handleDeleteStringArrayItem(
  violationIndex: number,
  field: 'discretion_standard' | 'rectification_measures' | 'penalty_suggestion',
  itemIndex: number
) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const items = [...(updated[violationIndex][field] || [])]
  items.splice(itemIndex, 1)
  updated[violationIndex] = { ...updated[violationIndex], [field]: items }
  editedViolations.value = updated
}

function handleAddStringArrayItem(
  violationIndex: number,
  field: 'discretion_standard' | 'rectification_measures' | 'penalty_suggestion'
) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const items = [...(updated[violationIndex][field] || [])]
  items.push('')
  updated[violationIndex] = { ...updated[violationIndex], [field]: items }
  editedViolations.value = updated
}

function toggleViolationExpand(violationId: number) {
  const newSet = new Set(expandedViolationIds.value)
  if (newSet.has(violationId)) {
    newSet.delete(violationId)
  } else {
    newSet.add(violationId)
  }
  expandedViolationIds.value = newSet
}

// 违法行为中的证据列表处理函数
function handleViolationEvidenceFieldChange(
  violationIndex: number,
  evidenceIndex: number,
  field: keyof EvidenceItem,
  value: string
) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const evidenceList = [...(updated[violationIndex].evidence_list || [])]
  evidenceList[evidenceIndex] = { ...evidenceList[evidenceIndex], [field]: value }
  updated[violationIndex] = { ...updated[violationIndex], evidence_list: evidenceList }
  editedViolations.value = updated
}

function handleDeleteViolationEvidence(violationIndex: number, evidenceIndex: number) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const evidenceList = [...(updated[violationIndex].evidence_list || [])]
  evidenceList.splice(evidenceIndex, 1)
  updated[violationIndex] = { ...updated[violationIndex], evidence_list: evidenceList }
  editedViolations.value = updated
}

function handleAddViolationEvidence(violationIndex: number) {
  isEditing.value = true
  const updated = [...editedViolations.value]
  const evidenceList = [...(updated[violationIndex].evidence_list || [])]
  evidenceList.push({
    evidence_name_code: '',
    evidence_source: '',
    evidence_form: '',
    evidence_truth: '',
  })
  updated[violationIndex] = { ...updated[violationIndex], evidence_list: evidenceList }
  editedViolations.value = updated
}

// 处理证据文件上传
async function handleEvidenceFileUpload(violationIndex: number, file: File) {
  try {
    // 校验文件类型
    const fileExt = file.name.toLowerCase().split('.').pop()
    if (fileExt !== 'docx' && fileExt !== 'wps') {
      alert('只能上传 Word 文档（.docx 或 .wps 格式）')
      return
    }

    // 创建 FormData
    const formData = new FormData()
    formData.append('file', file)
    formData.append('file_type_hint', '取证记录表')

    // 需要获取当前违法行为对应的 group_id 和 violation_id
    const currentViolation = editedViolations.value[violationIndex]
    let groupId = null
    const violationId = currentViolation.violation_id

    if (currentViolation.source_files && currentViolation.source_files.length > 0) {
      groupId = currentViolation.source_files[0].group_id
    }

    if (groupId) {
      formData.append('group_id', groupId)
    } else if (violationId) {
      formData.append('violation_id', violationId.toString())
    }

    // 调用上传回调（如果提供了）
    if (props.onUploadEvidence) {
      await props.onUploadEvidence(file, violationId)
    }

    // 调用上传 API
    const response = await fetch(`${API_BASE}/api/conversations/${props.threadId}/files`, {
      method: 'POST',
      credentials: 'include',
      body: formData,
    })

    if (!response.ok) {
      throw new Error(`上传失败: ${response.status} ${response.statusText}`)
    }

    const result = await response.json()

    if (result.status === 'error') {
      alert(result.msg || '文件上传失败')
      return
    }

    // 上传成功，从返回的数据中获取解析结果
    const uploadedFile = result.data

    // 立即更新当前违法行为的数据
    isEditing.value = true
    const updated = [...editedViolations.value]
    const currentViolationUpdated = updated[violationIndex]

    // 添加取证记录表到附件列表
    const newAttachment = {
      file_id: uploadedFile.file_id,
      filename: uploadedFile.file_name,
      file_name: uploadedFile.file_name,
      file_type: '取证记录表',
      upload_time: new Date().toISOString(),
      group_id: groupId,
    }

    if (!currentViolationUpdated.source_files) {
      currentViolationUpdated.source_files = []
    }

    // 检查是否已存在取证记录表，避免重复添加
    const hasEvidenceRecord = currentViolationUpdated.source_files.some(
      (f: any) => f.file_type === '取证记录表'
    )

    if (!hasEvidenceRecord) {
      currentViolationUpdated.source_files.push(newAttachment)
    } else {
      const index = currentViolationUpdated.source_files.findIndex(
        (f: any) => f.file_type === '取证记录表'
      )
      currentViolationUpdated.source_files[index] = newAttachment
    }

    // 如果后端返回了解析后的证据列表，也更新到界面
    if (uploadedFile.parsed_data?.evidence_list) {
      currentViolationUpdated.evidence_list = uploadedFile.parsed_data.evidence_list
    }

    updated[violationIndex] = currentViolationUpdated
    editedViolations.value = updated

    // 更新该违法行为的取证记录表上传状态
    // 使用 Vue.set 或重新赋值整个对象确保响应式更新
    const newStatus = { ...violationUploadStatus.value, [violationId]: true }
    violationUploadStatus.value = newStatus
  } catch (error) {
    console.error('[证据上传] 失败:', error)
    alert('证据上传失败，请稍后重试')
  }
}

function handleEvidenceFieldChange(index: number, field: keyof EvidenceItem, value: string) {
  isEditing.value = true
  const newList = [...editedEvidenceData.value.evidence_list]
  newList[index] = { ...newList[index], [field]: value }
  editedEvidenceData.value = { ...editedEvidenceData.value, evidence_list: newList }
}

function handleDeleteEvidence(index: number) {
  isEditing.value = true
  editedEvidenceData.value = {
    ...editedEvidenceData.value,
    evidence_list: editedEvidenceData.value.evidence_list.filter((_, i) => i !== index)
  }
}

function handleAddEvidence() {
  isEditing.value = true
  editedEvidenceData.value = {
    ...editedEvidenceData.value,
    evidence_list: [
      ...editedEvidenceData.value.evidence_list,
      { evidence_name_code: '', evidence_source: '', evidence_form: '', evidence_truth: '' }
    ]
  }
}

// 确认和取消
const isConfirmDisabled = computed(() => {
  // 如果需要证据上传，检查是否所有违法行为都已上传取证记录表
  if (props.reviewData?.requires_evidence_upload && !props.reviewData?.all_evidence_uploaded) {
    const status = violationUploadStatus.value
    const values = Object.values(status)
    const allUploaded = values.every(Boolean)
    return !allUploaded
  }
  return false
})

const confirmDisabledTitle = computed(() => {
  if (props.reviewData?.requires_evidence_upload && !props.reviewData?.all_evidence_uploaded) {
    const allUploaded = Object.values(violationUploadStatus.value).every(Boolean)
    if (!allUploaded) {
      return '请为每个违法行为上传取证记录表'
    }
  }
  return ''
})

function handleConfirm() {
  let data: ReviewEditData
  if (reviewType.value === 'extract') {
    data = editedExtractData.value as ReviewEditData
  } else if (reviewType.value === 'evidence') {
    data = editedEvidenceData.value as ReviewEditData
  } else {
    // violations 类型 (split/analyze/retrieve)
    data = { violations: editedViolations.value }
  }
  emit('confirm', data, isEditing.value)
}

function handleCancel() {
  emit('cancel')
}
</script>
