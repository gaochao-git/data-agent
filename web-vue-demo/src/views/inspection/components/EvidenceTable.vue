<template>
  <div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
      <thead class="bg-gray-50 dark:bg-gray-800">
        <tr>
          <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">序号</th>
          <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">证据名称及编号</th>
          <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">证据来源</th>
          <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">证据形式</th>
          <th class="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">真实性说明</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
        <tr
          v-for="(item, idx) in evidence"
          :key="idx"
          class="hover:bg-gray-50 dark:hover:bg-gray-800"
        >
          <td class="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{{ idx + 1 }}</td>
          <td class="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{{ item.evidence_name_code }}</td>
          <td class="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{{ item.evidence_source }}</td>
          <td class="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{{ item.evidence_form }}</td>
          <td class="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{{ item.evidence_truth }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
interface EvidenceItem {
  evidence_name_code: string
  evidence_source: string
  evidence_form: string
  evidence_truth: string
  provider_sign?: string
  remark?: string
}

interface Props {
  evidence: EvidenceItem[]
}

defineProps<Props>()
</script>
