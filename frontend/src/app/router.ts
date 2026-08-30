import { createRouter, createWebHistory } from 'vue-router'

import { migratedRoutes } from './route-boundaries'

export const router = createRouter({
  history: createWebHistory(),
  routes: migratedRoutes,
})
