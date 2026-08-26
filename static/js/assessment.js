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
  const confidencePanel = document.getElementById('confidence-panel');
  const confidenceInput = document.getElementById('confidence-probability');
  const confidenceValue = document.getElementById('confidence-value');
  const confidenceStatus = document.getElementById('confidence-status');
  const confidenceSubmit = document.getElementById('confidence-submit-btn');
  const confidenceSkip = document.getElementById('confidence-skip-btn');
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;
  const draft = JSON.parse(document.getElementById('draft-data').textContent || 'null');
  const pendingConfidence = JSON.parse(
    document.getElementById('pending-confidence-data')?.textContent || 'null',
  );
  let debounceTimer = null;
  let lastSerialized = null;
  let lastQueuedSerialized = null;
  let saving = null;
  let saveQueue = Promise.resolve();
  let submitting = false;
  let inputStarted = false;
  let processLifecycle = null;
  let confidencePromptPromise = null;

  const processApi = window.KTProcessData;
  const processReady = (async () => {
    if (!processApi) return null;
    const outbox = await processApi.defaultOutbox();
    const collector = new processApi.ProcessEventCollector({
      sessionId: Number(form.dataset.processSessionId),
      attemptId: Number(form.dataset.processAttemptId),
      endpoint: form.dataset.processEventUrl,
      csrfToken: csrf,
      appVersion: form.dataset.processAppVersion,
      outbox,
    });
    const syncUrls = {
      createUrl: form.dataset.timeSyncCreateUrl,
      finalizeUrl: form.dataset.timeSyncFinalizeUrl,
    };
    processLifecycle = processApi.bindLifecycle(collector, {
      heartbeatIntervalSeconds: Number(form.dataset.heartbeatIntervalSeconds),
      attemptOpen: !['submitted', 'timed_out', 'unanswered'].includes(form.dataset.attemptStatus),
      onlineOptions: { syncUrls },
    });
    await processLifecycle.ready;
    collector.synchronizeClock(syncUrls).then(() => collector.flush());
    collector.startSynchronization(syncUrls, processApi.DEFAULT_SYNC_INTERVAL_MS, false);
    return collector;
  })().catch(() => null);

  async function recordProcessEvent(eventType, payload, options = {}) {
    try {
      const collector = await processReady;
      if (!collector) return null;
      return await collector.record(eventType, payload, options);
    } catch (_error) {
      return null;
    }
  }

  async function closeProcessAttempt(reason) {
    try {
      await processReady;
      await processLifecycle?.closeAttempt(reason);
    } catch (_error) {
      // Process-data capture must never block the authoritative assessment flow.
    }
  }

  function hideSubmitDialog() {
    const modal = window.bootstrap?.Modal.getOrCreateInstance(submitDialog);
    if (!modal) return;
    const hideAfterShow = () => modal.hide();
    submitDialog.addEventListener('shown.bs.modal', hideAfterShow, { once: true });
    modal.hide();
  }

  const escapeCss = (value) => window.CSS && CSS.escape ? CSS.escape(value) : value.replace(/["']/g, '\\$&');
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[character]));
  const makeToken = () => window.crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}-4000-8000-${Math.random().toString(16).slice(2).padEnd(12, '0').slice(0, 12)}`;

  function formatCorrectAnswer(value) {
    if (form.dataset.questionType === 'true_false' && typeof value === 'boolean') {
      return value ? '正确' : '错误';
    }
    if (Array.isArray(value)) return value.join(', ');
    return value === null || value === undefined ? '' : String(value);
  }

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
    if (type === 'single_choice' || type === 'true_false') return form.querySelector('input[name="answer"]:checked')?.value || null;
    if (type === 'multiple_choice') return [...form.querySelectorAll('input[name="answer"]:checked')].map((item) => item.value).sort();
    return form.querySelector('[name="answer"]')?.value ?? null;
  }

  function restore() {
    if (draft === null || draft === undefined) return;
    const type = form.dataset.questionType;
    if (type === 'single_choice' || type === 'true_false') {
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
    if (!response.ok || !data.success) {
      const message = typeof data.error === 'string'
        ? data.error
        : data.error?.message || '请求失败，请稍后重试。';
      throw new Error(message);
    }
    return data;
  }

  function lockAnswerControls() {
    form.querySelectorAll('[name="answer"]').forEach((element) => {
      element.disabled = true;
    });
    skipBtn?.remove();
    if (submitBtn) {
      submitBtn.closest('.sticky-actions')?.remove();
    }
  }

  async function renderFeedback(data) {
    let details = '';
    if (data.correct_answer !== undefined) {
      details += `<div class="mt-2"><strong>正确答案：</strong>${escapeHtml(formatCorrectAnswer(data.correct_answer))}</div>`;
    }
    if (data.explanation) {
      details += `<div class="mt-2"><strong>解析：</strong>${escapeHtml(data.explanation)}</div>`;
    }
    const correctness = data.is_correct === true
      ? '，回答正确'
      : data.is_correct === false ? '，回答错误' : '';
    feedback.innerHTML = `<div class="alert alert-success mb-0">本题已提交并锁定${correctness}${details}</div>`;
    await recordProcessEvent('feedback_shown', {
      feedback_mode: data.feedback_mode || 'none',
      included_correctness: data.is_correct !== undefined,
      included_correct_answer: data.correct_answer !== undefined,
      included_explanation: Boolean(data.explanation),
    });
    if (data.explanation) {
      await recordProcessEvent('explanation_shown', {
        source: 'submission_feedback',
      });
    }
  }

  async function showConfidencePrompt(contract) {
    if (!confidencePanel || !confidenceInput || !confidenceStatus) {
      throw new Error('信心评价界面加载失败，请刷新页面后重试。');
    }
    confidencePanel.dataset.commitUrl = contract.commit_url;
    confidencePanel.dataset.skipUrl = contract.skip_url;
    confidenceInput.min = String(contract.minimum);
    confidenceInput.max = String(contract.maximum);
    confidenceInput.step = String(contract.step);
    confidencePanel.hidden = false;
    confidenceStatus.textContent = '正在确认评价表已显示';
    confidenceStatus.className = 'save-status is-saving mt-2';
    confidenceInput.focus();
    confidencePanel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    confidencePromptPromise = post(contract.prompt_shown_url, {})
      .then(() => {
        confidenceStatus.textContent = '请选择把握程度，或跳过评价';
        confidenceStatus.className = 'save-status mt-2';
        return true;
      })
      .catch((error) => {
        confidenceStatus.textContent = '评价表状态同步失败，请重试';
        confidenceStatus.className = 'save-status is-error mt-2';
        throw error;
      });
    return confidencePromptPromise;
  }

  async function resolveConfidence(url, payload) {
    confidenceSubmit.disabled = true;
    confidenceSkip.disabled = true;
    confidenceStatus.textContent = '正在保存评价';
    confidenceStatus.className = 'save-status is-saving mt-2';
    clearError();
    try {
      await confidencePromptPromise;
      const data = await post(url, payload);
      confidencePanel.hidden = true;
      await renderFeedback(data);
      window.setTimeout(() => { window.location.href = data.next_url; }, 650);
    } catch (error) {
      showError(error.message, true);
      confidenceSubmit.disabled = false;
      confidenceSkip.disabled = false;
      confidenceStatus.textContent = '保存失败，可重新提交或跳过';
      confidenceStatus.className = 'save-status is-error mt-2';
    }
  }

  async function recordSavedResponse(data, source) {
    if (!data) return;
    const payload = {
      question_type: form.dataset.questionType,
      change_source: source,
    };
    if (Object.prototype.hasOwnProperty.call(data, 'response_state_after')) {
      payload.response_state_after = data.response_state_after;
    } else if (data.response_snapshot_id) {
      payload.response_snapshot_id = data.response_snapshot_id;
      payload.content_hash = data.content_hash;
      payload.content_length = data.content_length;
    } else {
      return;
    }
    await recordProcessEvent('response_state_changed', payload);
  }

  function saveDraft(force = false) {
    const serialized = JSON.stringify(collect());
    if (!force && serialized === lastQueuedSerialized) return saving || Promise.resolve(null);
    const answer = JSON.parse(serialized);
    lastQueuedSerialized = serialized;
    setSaveState('正在保存', 'saving');
    const operation = async () => {
      try {
        const data = await post(form.dataset.saveUrl, {
          answer,
          client_occurred_at: new Date().toISOString(),
        });
        lastSerialized = serialized;
        await recordSavedResponse(data, force ? 'forced_snapshot' : 'student');
        setSaveState('草稿已保存', 'saved');
        clearError();
        return data;
      } catch (error) {
        if (lastQueuedSerialized === serialized) lastQueuedSerialized = lastSerialized;
        setSaveState('草稿保存失败', 'error');
        showError(`${error.message} 当前答案仍保留在本页，请不要关闭页面。`);
        throw error;
      }
    };
    const result = saveQueue.then(operation, operation);
    saveQueue = result.catch(() => {});
    saving = result;
    return result.finally(() => {
      if (saving === result) saving = null;
    });
  }

  const choiceQuestion = ['single_choice', 'multiple_choice', 'true_false'].includes(
    form.dataset.questionType,
  );

  form.addEventListener('input', (event) => {
    if (!event.target.matches('[name="answer"]')) return;
    if (!inputStarted) {
      inputStarted = true;
      recordProcessEvent('response_input_started', {
        question_type: form.dataset.questionType,
      });
    }
    if (choiceQuestion) return;
    window.clearTimeout(debounceTimer);
    setSaveState('等待自动保存');
    debounceTimer = window.setTimeout(() => saveDraft().catch(() => {}), 800);
  });

  if (choiceQuestion) {
    form.addEventListener('change', (event) => {
      if (!event.target.matches('[name="answer"]')) return;
      setSaveState('正在保存', 'saving');
      saveDraft().catch(() => {});
    });
  }

  submitConfirm?.addEventListener('click', async () => {
    if (submitting) return;
    submitting = true;
    submitConfirm.disabled = true;
    submitConfirm.textContent = '正在提交';
    submitBtn.disabled = true;
    form.setAttribute('aria-busy', 'true');
    clearError();
    hideSubmitDialog();
    try {
      await saveDraft(true);
      const submissionToken = makeToken();
      await recordProcessEvent(
        'response_commit_requested',
        { submission_token: submissionToken },
        { correlationId: submissionToken },
      );
      const data = await post(form.dataset.submitUrl, {
        submission_token: submissionToken,
        client_clicked_at: new Date().toISOString()
      });
      lockAnswerControls();
      await closeProcessAttempt('submit');
      form.removeAttribute('aria-busy');
      if (data.confidence_required) {
        showConfidencePrompt(data.confidence).catch((error) => {
          showError(error.message, true);
        });
        submitting = false;
        return;
      }
      await renderFeedback(data);
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
      await closeProcessAttempt('skip');
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
      await closeProcessAttempt('navigation');
      window.location.href = link.href;
    } catch (error) {
      showError('草稿保存失败，尚未切换题目。请检查网络后重试。', true);
    }
  }));

  restore();
  lastSerialized = JSON.stringify(collect());
  lastQueuedSerialized = lastSerialized;
  if (pendingConfidence) {
    lockAnswerControls();
    showConfidencePrompt(pendingConfidence).catch((error) => {
      showError(error.message, true);
    });
  }

  const finishForm = document.querySelector('[data-finish-form]');
  finishForm?.addEventListener('assessment:finish-requested', async (event) => {
    event.preventDefault();
    try {
      if (submitBtn) await saveDraft(true);
      await closeProcessAttempt('finish');
      finishForm.submit();
    } catch (_error) {
      const finishConfirm = document.querySelector(
        '#finish-confirm-dialog [data-confirm-action]',
      );
      if (finishConfirm) {
        finishConfirm.disabled = false;
        finishConfirm.textContent = '结束实验';
      }
      showError('草稿保存失败，实验尚未结束。请检查网络后重试。', true);
    }
  });

  confidenceInput?.addEventListener('input', () => {
    confidenceValue.textContent = `${confidenceInput.value}%`;
  });

  confidenceSubmit?.addEventListener('click', () => resolveConfidence(
    confidencePanel.dataset.commitUrl,
    { probability: Number(confidenceInput.value) },
  ));

  confidenceSkip?.addEventListener('click', () => resolveConfidence(
    confidencePanel.dataset.skipUrl,
    {},
  ));

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
        closeProcessAttempt('timeout').finally(() => {
          window.setTimeout(() => window.location.reload(), 1200);
        });
      }
    }, 1000);
  }
})();
