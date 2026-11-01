<script setup lang="ts">
import {computed} from 'vue'
import {useRoute} from 'vue-router'
import SiteWorkspaceShell from '@/features/management-shared/SiteWorkspaceShell.vue'
import AdminOverview from './AdminOverview.vue'
import AdminUsers from './AdminUsers.vue'
import AdminCourses from './AdminCourses.vue'
import AdminCatalog from './AdminCatalog.vue'
const route=useRoute()
const section=computed(()=>({'users':'users','courses':'course','resources':'bank','data':'data','audit':'audit'} as Partial<Record<string,'users'|'course'|'bank'|'data'|'audit'>>)[route.path.split('/')[2]||'']||'overview')
</script>
<template><SiteWorkspaceShell mode="admin" :active="section"><AdminOverview v-if="section==='overview'" /><AdminUsers v-else-if="section==='users'" /><AdminCourses v-else-if="section==='course'" /><AdminCatalog v-else :section="section" /></SiteWorkspaceShell></template>
<style scoped>
:deep(.management-action-link){display:inline-flex;align-items:center;justify-content:center;border:1px solid #e3e9f0;padding:10px 16px;border-radius:7px;background:white;font-size:13px;min-height:40px}:deep(.management-action-link.primary){background:#182d49;color:white}:deep(.management-text-link){color:#285dbb;padding:6px 9px;font-size:13px}:deep(.cell-sub){max-width:360px;white-space:normal}:deep(.error-block){margin-bottom:18px}:deep(.detail-list dd){white-space:pre-wrap}
</style>
