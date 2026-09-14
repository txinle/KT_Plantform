<script setup lang="ts">
const policeRegistrationIcon = '/static/icons/beian-police.png'
</script>

<template>
  <footer
    data-site-registration-footer
    class="kt-site-registration"
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
