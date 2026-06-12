import { checkPermi } from '@/utils/permission'

function updateVisibility(el, value) {
  const allowed = checkPermi(value)
  el.style.display = allowed ? (el.dataset.hasPermiDisplay || '') : 'none'
}

export default {
  mounted(el, binding) {
    el.dataset.hasPermiDisplay = el.style.display || ''
    updateVisibility(el, binding.value)
  },
  updated(el, binding) {
    updateVisibility(el, binding.value)
  },
}
