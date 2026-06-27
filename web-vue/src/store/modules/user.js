import { defineStore } from 'pinia'

const DEV_USER = {
  id: 'chatdata_user',
  name: 'chatdata_user',
  nickName: 'chatdata_user',
  roles: ['admin'],
  permissions: ['chatdata:admin:manage'],
}

const useUserStore = defineStore('user', {
  state: () => ({
    ...DEV_USER,
  }),

  getters: {
    userId: (state) => state.id,
    userName: (state) => state.name || state.nickName,
    userPermissions: (state) => state.permissions || [],
    userRoles: (state) => state.roles || [],
  },

  actions: {
    async getInfo() {
      return {
        ...DEV_USER,
      }
    },
  },
})

export default useUserStore
