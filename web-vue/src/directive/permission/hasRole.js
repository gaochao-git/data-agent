import { checkRole } from '@/utils/permission'

function updateVisibility(el, value) {
  const allowed = checkRole(value)
  el.style.display = allowed ? (el.dataset.hasRoleDisplay || '') : 'none'
}

export default {
  mounted(el, binding) {
    el.dataset.hasRoleDisplay = el.style.display || ''
    updateVisibility(el, binding.value)
  },
  updated(el, binding) {
    updateVisibility(el, binding.value)
  },
}
