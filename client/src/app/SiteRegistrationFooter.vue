<script setup lang="ts">
defineProps<{ sidebar?: boolean }>()
const policeRegistrationIcon = '/static/spa-assets/icons/beian-police.png'
</script>

<template>
  <footer
    data-site-registration-footer
    class="kt-site-registration"
    :class="{ 'kt-site-registration--sidebar': sidebar }"
    aria-label="网站备案信息"
  >
    <div class="kt-site-registration__inner">
      <a
        data-icp-registration
        href="https://beian.miit.gov.cn/"
        rel="noreferrer"
        target="_blank"
      >湘ICP备2026036826号-1</a>
      <span class="kt-site-registration__divider" aria-hidden="true">·</span>
      <a
        data-police-registration
        class="kt-site-registration__police"
        href="https://beian.mps.gov.cn/#/query/webSearch?code=43010302002391"
        rel="noreferrer"
        target="_blank"
      >
        <img :src="policeRegistrationIcon" alt="" width="20" height="20">
        <span>湘公网安备43010302002391号</span>
      </a>
    </div>
  </footer>
</template>

<style scoped>
.kt-site-registration {
  flex: none;
  border-top: 1px solid var(--kt-border);
  padding: 16px 24px;
  background: var(--kt-surface);
  color: var(--kt-muted);
  font-size: var(--kt-font-sm);
}

.kt-site-registration__inner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
}

.kt-site-registration a {
  color: inherit;
  text-decoration: none;
}

.kt-site-registration a:hover {
  color: var(--kt-action-600);
  text-decoration: underline;
}

.kt-site-registration__police {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.kt-site-registration__police img {
  display: block;
  flex: none;
}

.kt-site-registration--sidebar {
  margin-top: auto;
  padding: 18px 0 0;
  font-size: 12px;
  line-height: 1.6;
}

.kt-site-registration--sidebar .kt-site-registration__inner {
  flex-direction: column;
  align-items: center;
  gap: 8px;
  text-align: center;
}

.kt-site-registration--sidebar .kt-site-registration__divider { display: none; }
.kt-site-registration--sidebar .kt-site-registration__police { align-items: flex-start; gap: 4px; }
.kt-site-registration--sidebar .kt-site-registration__police img { width: 14px; height: 14px; flex: none; margin-top: 2px; }
.kt-site-registration--sidebar a { min-width: 0; overflow-wrap: anywhere; }

@media (max-width: 480px) {
  .kt-site-registration__inner {
    flex-direction: column;
    gap: 6px;
  }

  .kt-site-registration__divider {
    display: none;
  }
}
</style>
