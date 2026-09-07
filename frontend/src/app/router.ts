import { createRouter, createWebHistory } from 'vue-router'

import LoginPage from './LoginPage.vue'
import { migratedRoutes } from './route-boundaries'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login/', name: 'login', component: LoginPage },
    ...migratedRoutes,
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./PageUnavailable.vue') },
  ],
})
