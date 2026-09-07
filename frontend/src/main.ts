import './design-system/foundations.css'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './app/App.vue'
import { initializeBootstrapContext } from './app/bootstrap'
import { router } from './app/router'

function isLoginPath() {
  return window.location.pathname === '/login/'
}

function loginUrl() {
  const currentPage = `${window.location.pathname}${window.location.search}${window.location.hash}`
  return `/login/?next=${encodeURIComponent(currentPage)}`
}

export async function startApplication() {
  let context
  try {
    context = await initializeBootstrapContext()
  } catch {
    const root = document.getElementById('kt-app')
    if (root) {
      const panel = document.createElement('main')
      panel.className = 'kt-shell'
      const heading = document.createElement('h1')
      heading.textContent = '暂时无法连接平台'
      const description = document.createElement('p')
      description.setAttribute('role', 'alert')
      description.textContent = '请检查网络后重试。本机已保存的作答记录不会被清除。'
      const retry = document.createElement('button')
      retry.className = 'kt-button kt-button--primary'
      retry.textContent = '重新连接'
      retry.addEventListener('click', () => { window.location.reload() })
      panel.append(heading, description, retry)
      root.replaceChildren(panel)
    }
    return
  }
  if (!context && !isLoginPath()) {
    window.location.assign(loginUrl())
    return
  }
  if (context && isLoginPath()) {
    window.location.assign(context.user.home_url)
    return
  }
  createApp(App).use(createPinia()).use(router).mount('#kt-app')
}

if (document.getElementById('kt-app')) void startApplication()
