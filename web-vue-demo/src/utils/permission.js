import useUserStore from '@/store/modules/user'

export function checkPermi(value) {
  if (value && value instanceof Array && value.length > 0) {
    const permissions = useUserStore().permissions || []
    const permissionData = value
    const allPermission = '*:*:*'
    return permissions.some((permission) => allPermission === permission || permissionData.includes(permission))
  }
  console.error("need permissions! Like checkPermi=['chatdata:datasource:manage']")
  return false
}

export function checkRole(value) {
  if (value && value instanceof Array && value.length > 0) {
    const roles = useUserStore().roles || []
    const permissionRoles = value
    const superAdmin = 'admin'
    return roles.some((role) => superAdmin === role || permissionRoles.includes(role))
  }
  console.error("need roles! Like checkRole=['admin']")
  return false
}
