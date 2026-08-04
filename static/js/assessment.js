(function () {
  'use strict';

  const form = document.getElementById('question-form');
  if (!form) return;

  const status = document.getElementById('save-status');
  const submitBtn = document.getElementById('submit-btn');
  const skipBtn = document.getElementById('skip-btn');
  const errorRegion = document.getElementById('operation-error');
  const feedback = document.getElementById('feedback');
  const submitDialog = document.getElementById('submit-confirm-dialog');
  const submitConfirm = submitDialog?.querySelector('[data-confirm-action]');
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;
  const draft = JSON.parse(document.getElementById('draft-data').textContent || 'null');
  let debounceTimer = null;
  let lastSerialized = null;
  let saving = null;
  let submitting = false;

  const escapeCss = (value) => window.CSS && CSS.escape ? CSS.escape(value) : value.replace(/["']/g, '\\$&');
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[character]));
  const makeToken = () => window.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}-4000-8000-${Math.random().toString(16).slice(2).padEnd(12, '0').slice(0, 12)}`;

  function setSaveState(message, state = '') {
    status.textContent = message;
    status.className = `save-status${state ? ` is-${state}` : ''}`;
  }

  function showError(message, focus = false) {
    errorRegion.textContent = message;
    errorRegion.classList.add('is-visible');
    if (focus) {
      errorRegion.setAttribute('tabindex', '-1');
      errorRegion.focus();
    }
  }

  function clearError() {
    errorRegion.textContent = '';
    errorRegion.classList.remove('is-visible');
    errorRegion.removeAttribute('tabindex');
  }

  function collect() {
    const type = form.dataset.questionType;
    if (type === 'single_choice') return form.querySelector('input[name="answer"]:checked')?.value || null;
    if (type === 'multiple_choice') return [...form.querySelectorAll('input[name="answer"]:checked')].map((item) => item.value).sort();
    return form.querySelector('[name="answer"]')?.value ?? null;
  }

  function restore() {
    if (draft === null || draft === undefined) return;
    const type = form.dataset.questionType;
    if (type === 'single_choice') {
      const element = form.querySelector(`input[value="${escapeCss(String(draft))}"]`);
      if (element) element.checked = true;
    } else if (type === 'multiple_choice') {
      (Array.isArray(draft) ? draft : []).forEach((value) => {
        const element = form.querySelector(`input[value="${escapeCss(String(value))}"]`);
        if (element) element.checked = true;
      });
    } else {
      const element = form.querySelector('[name="answer"]');
      if (element) element.value = draft;
    }
  }

  async function post(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify(payload)
    });
    let data;
    try {
      data = await response.json();
    } catch (error) {
      throw new Error('服务器返回了无法识别的响应，请稍后重试。');
    }
    if (!response.ok || !data.success) throw new Error(data.error || '请求失败，请稍后重试。');
    return data;
  }

  async function saveDraft(force = false) {
    const answer = collect();
    const serialized = JSON.stringify(answer);
    if (!force && serialized === lastSerialized) return;
    setSaveState('正在保存', 'saving');
    saving = post(form.dataset.saveUrl, { answer, client_occurred_at: new Date().toISOString() });
    try {
      await saving;
      lastSerialized = serialized;
      setSaveState('草稿已保存', 'saved');
      clearError();
    } catch (error) {
      setSaveState('草稿保存失败', 'error');
      showError(`${error.message} 当前答案仍保留在本页，请不要关闭页面。`);
      throw error;
    } finally {
      saving = null;
    }
  }

  form.addEventListener('input', () => {
    window.clearTimeout(debounceTimer);
    setSaveState('等待自动保存');
    debounceTimer = window.setTimeout(() => saveDraft().catch(() => {}), 800);
  });

  submitConfirm?.addEventListener('click', async () => {
    if (submitting) return;
    submitting = true;
    submitConfirm.disabled = true;
    submitConfirm.textContent = '正在提交';
    submitBtn.disabled = true;
    form.setAttribute('aria-busy', 'true');
    clearError();
    window.bootstrap?.Modal.getOrCreateInstance(submitDialog).hide();
    try {
      await saveDraft(true);
      const data = await post(form.dataset.submitUrl, {
        submission_token: makeToken(),
        client_clicked_at: new Date().toISOString()
      });
      let details = '';
      if (data.correct_answer !== undefined) details += `<div class="mt-2"><strong>正确答案：</strong>${escapeHtml(JSON.stringify(data.correct_answer))}</div>`;
      if (data.explanation) details += `<div class="mt-2"><strong>解析：</strong>${escapeHtml(data.explanation)}</div>`;
      const correctness = data.is_correct === true ? '，回答正确' : data.is_correct === false ? '，回答错误' : '';
      feedback.innerHTML = `<div class="alert alert-success mb-0">本题已提交并锁定${correctness}${details}</div>`;
      form.querySelectorAll('input, textarea').forEach((element) => { element.disabled = true; });
      skipBtn?.remove();
      submitBtn.textContent = '已提交';
      window.setTimeout(() => { window.location.href = data.next_url; }, 650);
    } catch (error) {
      showError(error.message, true);
      submitting = false;
      submitConfirm.disabled = false;
      submitConfirm.textContent = '提交并锁定';
      submitBtn.disabled = false;
      form.removeAttribute('aria-busy');
    }
  });

  skipBtn?.addEventListener('click', async () => {
    skipBtn.disabled = true;
    skipBtn.textContent = '正在保存';
    clearError();
    try {
      await saveDraft(true);
      const data = await post(form.dataset.skipUrl, {});
      window.location.href = data.next_url;
    } catch (error) {
      showError(error.message, true);
      skipBtn.disabled = false;
      skipBtn.textContent = '标记跳过';
    }
  });

  document.querySelectorAll('.js-question-nav').forEach((link) => link.addEventListener('click', async (event) => {
    if (link.classList.contains('current')) return;
    event.preventDefault();
    clearError();
    try {
      if (submitBtn) await saveDraft(true);
      window.location.href = link.href;
    } catch (error) {
      showError('草稿保存失败，尚未切换题目。请检查网络后重试。', true);
    }
  }));

  restore();
  lastSerialized = JSON.stringify(collect());

  const timerElement = document.getElementById('countdown');
  if (timerElement) {
    let seconds = Number(timerElement.dataset.seconds);
    const renderTime = () => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
    timerElement.textContent = renderTime();
    const clock = window.setInterval(() => {
      seconds = Math.max(0, seconds - 1);
      timerElement.textContent = renderTime();
      if (seconds <= 0) {
        window.clearInterval(clock);
        form.querySelectorAll('input, textarea, button').forEach((element) => { element.disabled = true; });
        showError('本题或实验时间已结束，页面将刷新并同步服务器状态。', true);
        window.setTimeout(() => window.location.reload(), 1200);
      }
    }, 1000);
  }
})();
