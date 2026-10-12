import { createRouter, createWebHistory } from 'vue-router'

import LoginPage from './LoginPage.vue'
import { migratedRoutes } from './route-boundaries'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./HomeEntry.vue') },
    { path: '/teacher/', name: 'teacher-home', component: () => import('./HomeEntry.vue') },
    { path: '/login/', name: 'login', component: LoginPage },
    { path: '/password/change/', name: 'password-change', component: () => import('./PasswordChangePage.vue') },
    ...migratedRoutes,
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./PageUnavailable.vue') },
  ],
})
