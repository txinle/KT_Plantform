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
  const submitDismissControls = submitDialog?.querySelectorAll('[data-bs-dismiss="modal"]') || [];
  const confidencePanel = document.getElementById('confidence-panel');
  const confidenceInput = document.getElementById('confidence-probability');
  const confidenceValue = document.getElementById('confidence-value');
  const confidenceStatus = document.getElementById('confidence-status');
  const confidenceSubmit = document.getElementById('confidence-submit-btn');
  const confidenceSkip = document.getElementById('confidence-skip-btn');
  const csrf = form.querySelector('[name="csrfmiddlewaretoken"]').value;
  const draft = JSON.parse(document.getElementById('draft-data').textContent || 'null');
  const initialResponseState = JSON.parse(
    document.getElementById('initial-response-state-data')?.textContent || 'null',
  );
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
  let lastStableText = typeof draft === 'string' ? draft : '';
  let textEditQueue = Promise.resolve();
  let textObservationTimer = null;
  let pendingTextObservation = null;
  let processLifecycle = null;
  let processOutbox = null;
  let processInitializationError = null;
  let confidencePromptPromise = null;
  let submissionConfidence = null;
  let confirmationAnswer = null;
  let confidenceTouched = false;
  let confirmationGeneration = 0;
  let pendingSubmission = null;
  let submissionIntentLoaded = false;
  let submissionOutcomeUnknown = false;
  let recoveryPromise = null;
  let submissionAccepted = form.dataset.attemptStatus === 'submitted';
  let lastRecordedResponseState = initialResponseState;

  if (submitBtn) submitBtn.disabled = true;

  const processApi = window.KTProcessData;
  const processReady = (async () => {
    if (!processApi) return null;
    const outbox = await processApi.defaultOutbox();
    processOutbox = outbox;
    const collector = new processApi.ProcessEventCollector({
      sessionId: Number(form.dataset.processSessionId),
      attemptId: Number(form.dataset.processAttemptId),
      endpoint: form.dataset.processEventUrl,
      checkpointEndpoint: form.dataset.captureCheckpointUrl,
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
  })().catch((error) => {
    processInitializationError = error;
    return null;
  });

  async function recordProcessEvent(eventType, payload, options = {}) {
    try {
      const collector = await processReady;
      if (!collector) return null;
      return await collector.record(eventType, payload, options);
    } catch (_error) {
      return null;
    }
  }

  async function sha256Text(value) {
    if (!window.crypto?.subtle || typeof TextEncoder === 'undefined') return null;
    const bytes = new TextEncoder().encode(value);
    const digest = await window.crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  function editOperation(inputType) {
    if (inputType === 'insertFromPaste') return 'paste';
    if (String(inputType || '').startsWith('delete')) return 'delete';
    if (inputType === 'insertReplacementText' || inputType === 'insertFromDrop') return 'replace';
    if (String(inputType || '').startsWith('insert')) return 'insert';
    return 'unknown';
  }

  function inputMethod(inputType, composing) {
    if (composing || String(inputType || '').includes('Composition')) return 'ime';
    if (inputType === 'insertFromPaste') return 'paste';
    return inputType ? 'keyboard' : 'unknown';
  }

  function flushStableTextObservation() {
    window.clearTimeout(textObservationTimer);
    textObservationTimer = null;
    const observation = pendingTextObservation;
    pendingTextObservation = null;
    if (!observation || observation.after === observation.before) return textEditQueue;
    lastStableText = observation.after;
    textEditQueue = textEditQueue.then(async () => {
      const [beforeHash, afterHash] = await Promise.all([
        sha256Text(observation.before),
        sha256Text(observation.after),
      ]);
      if (!beforeHash || !afterHash) return;
      await recordProcessEvent('text_edit_observed', {
        question_type: form.dataset.questionType,
        edit_operation: observation.operation,
        content_hash_before: beforeHash,
        content_hash_after: afterHash,
        content_length_before: observation.before.length,
        content_length_after: observation.after.length,
        input_method: observation.method,
        batch_start_client_monotonic_ms: observation.startedAt,
        batch_end_client_monotonic_ms: observation.endedAt,
        is_ime: observation.isIme,
      });
    }).catch(() => {});
    return textEditQueue;
  }

  function queueStableTextObservation(element, event = {}) {
    if (!element || event.isComposing) return;
    const after = String(element.value ?? '');
    const operation = editOperation(event.inputType);
    const method = inputMethod(event.inputType, event.isComposing);
    const now = Math.round(performance.now() * 1000) / 1000;
    if (pendingTextObservation && pendingTextObservation.operation !== operation) {
      flushStableTextObservation();
    }
    if (!pendingTextObservation) {
      pendingTextObservation = {
        before: lastStableText,
        after,
        operation,
        method,
        isIme: method === 'ime',
        startedAt: now,
        endedAt: now,
      };
    } else {
      pendingTextObservation.after = after;
      pendingTextObservation.endedAt = now;
      pendingTextObservation.isIme ||= method === 'ime';
    }
    window.clearTimeout(textObservationTimer);
    textObservationTimer = window.setTimeout(flushStableTextObservation, 800);
  }

  async function closeProcessAttempt(reason, toAssignmentId = null) {
    try {
      await flushStableTextObservation();
      await processReady;
      await processLifecycle?.closeAttempt(reason, toAssignmentId);
    } catch (_error) {
      // Process-data capture must never block the authoritative assessment flow.
    }
  }

  function hideSubmitDialog() {
    const modal = window.bootstrap?.Modal.getOrCreateInstance(submitDialog);
    if (!modal) return;
    if (submitDialog.classList.contains('show')) {
      modal.hide();
      return;
    }
    const hideAfterShow = () => modal.hide();
    submitDialog.addEventListener('shown.bs.modal', hideAfterShow, { once: true });
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

  async function get(url) {
    const response = await fetch(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    });
    let data;
    try {
      data = await response.json();
    } catch (_error) {
      throw new Error('服务器返回了无法识别的核验结果，请稍后重试。');
    }
    if (!response.ok) {
      const message = typeof data.error === 'string'
        ? data.error
        : data.error?.message || '暂时无法核验提交结果，请稍后重试。';
      throw new Error(message);
    }
    return data;
  }

  async function submissionOutbox() {
    await processReady;
    if (
      !processOutbox?.saveSubmissionIntent
      || !processOutbox?.restoreSubmissionIntent
    ) {
      throw processInitializationError || new Error('本地提交记录不可用，请刷新页面后重试。');
    }
    return processOutbox;
  }

  async function persistSubmissionIntent(intent) {
    try {
      const outbox = await submissionOutbox();
      await outbox.saveSubmissionIntent(
        Number(form.dataset.processSessionId),
        Number(form.dataset.processAttemptId),
        intent,
      );
    } catch (_error) {
      throw new Error(intent
        ? '本地提交记录保存失败，请不要关闭页面，刷新后重试。'
        : '本地提交记录清理失败，请刷新页面后核验。');
    }
  }

  function submissionRecoveryUrl(token) {
    const separator = form.dataset.recoveryUrl.includes('?') ? '&' : '?';
    return `${form.dataset.recoveryUrl}${separator}submission_token=${encodeURIComponent(token)}`;
  }

  function setAnswerControlsDisabled(disabled) {
    form.querySelectorAll('[name="answer"]').forEach((element) => {
      element.disabled = disabled;
    });
  }

  function setSubmitDismissDisabled(disabled) {
    submitDismissControls.forEach((element) => {
      element.disabled = disabled;
    });
  }

  function lockAnswerControls() {
    setAnswerControlsDisabled(true);
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

  async function showConfidencePrompt(contract, generation) {
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
    confidencePromptPromise = post(contract.prompt_shown_url, {});
    try {
      await confidencePromptPromise;
      if (generation !== confirmationGeneration) return false;
      confidenceStatus.textContent = '请选择把握程度，或跳过评价';
      confidenceStatus.className = 'save-status mt-2';
      return true;
    } catch (error) {
      if (generation !== confirmationGeneration) return false;
      confidenceStatus.textContent = '评价表状态同步失败，请重试';
      confidenceStatus.className = 'save-status is-error mt-2';
      throw error;
    }
  }

  async function resolveConfidence(skip) {
    if (!skip && !confidenceTouched) return;
    const generation = confirmationGeneration;
    const confidence = skip
      ? { status: 'skipped' }
      : { status: 'committed', probability: Number(confidenceInput.value) };
    clearError();
    try {
      await confidencePromptPromise;
      if (generation !== confirmationGeneration) return;
      submissionConfidence = confidence;
      confidencePanel.hidden = true;
      window.bootstrap.Modal.getOrCreateInstance(submitDialog).show();
    } catch (error) {
      if (generation === confirmationGeneration) showError(error.message, true);
    }
  }

  async function returnToAnswer() {
    const clearStoredIntent = Boolean(pendingSubmission)
      || submissionOutcomeUnknown
      || !submissionIntentLoaded;
    confirmationGeneration += 1;
    pendingSubmission = null;
    submissionOutcomeUnknown = false;
    submissionConfidence = null;
    confirmationAnswer = null;
    confidenceTouched = false;
    confidencePanel.hidden = true;
    submitting = clearStoredIntent;
    form.removeAttribute('aria-busy');
    setAnswerControlsDisabled(false);
    setSubmitDismissDisabled(false);
    if (submitConfirm) {
      submitConfirm.disabled = false;
      submitConfirm.textContent = '提交并锁定';
    }
    if (clearStoredIntent) {
      try {
        await persistSubmissionIntent(null);
        submissionIntentLoaded = true;
      } catch (error) {
        showError(error.message, true);
        return false;
      }
    }
    submitting = false;
    if (submitBtn) submitBtn.disabled = false;
    return true;
  }
  document.getElementById('confidence-return-btn')?.addEventListener('click', returnToAnswer);
  submitDismissControls.forEach((element) => element.addEventListener('click', () => {
    if (!submitting && !submissionOutcomeUnknown) returnToAnswer();
  }));
  form.addEventListener('input', (event) => {
    if (event.target === confidenceInput || confidencePanel.contains(event.target)) return;
    if (submitting || submissionOutcomeUnknown) return;
    returnToAnswer();
  });
  submitBtn?.addEventListener('click', async () => {
    if (submissionAccepted || submissionOutcomeUnknown || submitting) return;
    if (pendingSubmission) {
      window.bootstrap.Modal.getOrCreateInstance(submitDialog).show();
      return;
    }
    confirmationGeneration += 1;
    const generation = confirmationGeneration;
    confirmationAnswer = JSON.stringify(collect());
    if (pendingConfidence) {
      confidenceTouched = false;
      confidenceSubmit.disabled = true;
      confidenceInput.value = '50';
      confidenceValue.textContent = '请选择';
      submissionConfidence = null;
      submitBtn.disabled = true;
      try { await showConfidencePrompt(pendingConfidence, generation); }
      catch (error) {
        if (generation === confirmationGeneration) showError(error.message, true);
      }
    } else window.bootstrap.Modal.getOrCreateInstance(submitDialog).show();
  });

  async function recordSavedResponse(data, source) {
    if (!data) return;
    const payload = {
      question_type: form.dataset.questionType,
      change_source: source,
    };
    if (!data.response_snapshot_id) {
      return;
    }
    payload.response_state_before = lastRecordedResponseState;
    payload.response_state_after = {
      snapshot_id: data.response_snapshot_id,
      state_hash: data.state_hash,
      content_hash: data.content_hash,
      content_length: data.content_length,
      schema_version: 1,
    };
    if (
      JSON.stringify(payload.response_state_before)
      === JSON.stringify(payload.response_state_after)
    ) return;
    const recorded = await recordProcessEvent('response_state_changed', payload);
    if (recorded) lastRecordedResponseState = payload.response_state_after;
  }

  function hasMeaningfulResponse(value) {
    if (Array.isArray(value)) return value.length > 0;
    return value !== null && value !== undefined && String(value) !== '';
  }

  function saveDraft(force = false) {
    const serialized = JSON.stringify(collect());
    if (!force && serialized === lastQueuedSerialized) return saving || Promise.resolve(null);
    const answer = JSON.parse(serialized);
    lastQueuedSerialized = serialized;
    setSaveState('正在保存', 'saving');
    const operation = async () => {
      try {
        if (form.dataset.questionType !== 'short_answer') {
          const nextState = answer;
          const changed = JSON.stringify(nextState) !== JSON.stringify(lastRecordedResponseState);
          const shouldRecord = changed && (
            lastRecordedResponseState !== null || hasMeaningfulResponse(nextState)
          );
          if (shouldRecord) {
            const recorded = await recordProcessEvent('response_state_changed', {
              question_type: form.dataset.questionType,
              response_state_before: lastRecordedResponseState,
              response_state_after: nextState,
              change_source: force ? 'forced_snapshot' : 'student',
            });
            if (recorded) {
              lastRecordedResponseState = JSON.parse(JSON.stringify(nextState));
            }
          }
        }
        const data = await post(form.dataset.saveUrl, {
          answer,
          client_occurred_at: new Date().toISOString(),
        });
        lastSerialized = serialized;
        if (form.dataset.questionType === 'short_answer') {
          await recordSavedResponse(data, force ? 'forced_snapshot' : 'student');
        }
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
    queueStableTextObservation(event.target, event);
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

  form.addEventListener('compositionend', (event) => {
    if (!event.target.matches('[name="answer"]') || choiceQuestion) return;
    queueStableTextObservation(event.target, { inputType: 'insertCompositionText' });
    flushStableTextObservation();
  });

  form.querySelector('[name="answer"]')?.addEventListener('blur', () => {
    if (!choiceQuestion) flushStableTextObservation();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && !choiceQuestion) {
      flushStableTextObservation();
    }
  });

  async function acceptSubmission(data) {
    let persistenceError = null;
    try {
      await persistSubmissionIntent(null);
      submissionIntentLoaded = true;
      pendingSubmission = null;
    } catch (error) {
      persistenceError = error;
    }
    submissionAccepted = true;
    submissionOutcomeUnknown = false;
    hideSubmitDialog();
    lockAnswerControls();
    await closeProcessAttempt('submit');
    form.removeAttribute('aria-busy');
    await renderFeedback(data);
    if (persistenceError) showError(persistenceError.message, true);
    window.setTimeout(() => { window.location.href = data.next_url; }, 650);
  }

  async function recoverSubmission() {
    if (!pendingSubmission) return;
    if (recoveryPromise) return recoveryPromise;
    const intent = pendingSubmission;
    submissionOutcomeUnknown = true;
    submitting = true;
    window.clearTimeout(debounceTimer);
    debounceTimer = null;
    setAnswerControlsDisabled(true);
    setSubmitDismissDisabled(true);
    if (submitBtn) submitBtn.disabled = true;
    submitConfirm.disabled = true;
    submitConfirm.textContent = '正在核验';
    form.setAttribute('aria-busy', 'true');
    showError('正在核验服务器上的提交结果…', true);
    window.bootstrap.Modal.getOrCreateInstance(submitDialog).show();
    recoveryPromise = (async () => {
      try {
        const recovered = await get(submissionRecoveryUrl(intent.token));
        if (recovered.result) {
          await acceptSubmission(recovered.result);
          return;
        }
        submissionOutcomeUnknown = false;
        submitting = false;
        form.removeAttribute('aria-busy');
        if (!recovered.can_continue) {
          let message = recovered.code === 'DEADLINE_EXPIRED'
            ? '作答时间已截止，本次答案未正式提交。'
            : '任务已关闭，本次答案未正式提交。';
          try {
            await persistSubmissionIntent(null);
            submissionIntentLoaded = true;
            pendingSubmission = null;
          } catch (error) {
            message = `${message} ${error.message}`;
          }
          lockAnswerControls();
          hideSubmitDialog();
          showError(message, true);
          return;
        }
        if (intent.answer !== JSON.stringify(collect())) {
          const cleared = await returnToAnswer();
          hideSubmitDialog();
          showError(cleared
            ? '服务器确认尚未提交。已保留当前草稿，请重新评价并确认。'
            : '服务器确认尚未提交，但本地提交记录清理失败，请刷新后核验。', true);
          return;
        }
        confirmationAnswer = intent.answer;
        submissionConfidence = intent.confidence || null;
        setSubmitDismissDisabled(false);
        submitConfirm.disabled = false;
        submitConfirm.textContent = '提交并锁定';
        if (submitBtn) submitBtn.disabled = false;
        window.bootstrap.Modal.getOrCreateInstance(submitDialog).show();
        showError('服务器确认尚未提交。可以重试本次提交，或返回修改答案；计时继续。', true);
      } catch (_error) {
        submitting = false;
        form.removeAttribute('aria-busy');
        submitConfirm.disabled = false;
        submitConfirm.textContent = '重新核验提交结果';
        showError('暂时无法核验提交结果，请恢复网络后重新核验。', true);
      } finally {
        recoveryPromise = null;
      }
    })();
    return recoveryPromise;
  }

  submitConfirm?.addEventListener('click', async () => {
    if (submissionOutcomeUnknown) {
      await recoverSubmission();
      return;
    }
    if (submitting) return;
    submitting = true;
    setAnswerControlsDisabled(true);
    setSubmitDismissDisabled(true);
    submitConfirm.disabled = true;
    submitConfirm.textContent = '正在提交';
    submitBtn.disabled = true;
    form.setAttribute('aria-busy', 'true');
    clearError();
    hideSubmitDialog();
    try {
      if (confirmationAnswer !== JSON.stringify(collect()) || (pendingConfidence && !submissionConfidence)) throw new Error('答案已变化，请重新评价并确认。');
      const submissionToken = pendingSubmission?.token || makeToken();
      const clickedAt = pendingSubmission?.clickedAt || new Date().toISOString();
      const intent = pendingSubmission || {
        token: submissionToken,
        clickedAt,
        answer: confirmationAnswer,
        confidence: submissionConfidence,
      };
      if (!pendingSubmission) {
        await persistSubmissionIntent(intent);
        submissionIntentLoaded = true;
        pendingSubmission = intent;
      }
      await saveDraft(true);
      await recordProcessEvent(
        'response_commit_requested',
        { submission_token: submissionToken },
        { correlationId: submissionToken },
      );
      const processCollector = await processReady;
      await processCollector?.checkpoint('commit');
      await processCollector?.flushBounded(1500, { ignoreVisibility: true, keepalive: true });
      const data = await post(form.dataset.submitUrl, {
        submission_token: submissionToken,
        client_clicked_at: clickedAt,
        ...(submissionConfidence ? { confidence: submissionConfidence } : {}),
      });
      await acceptSubmission(data);
    } catch (error) {
      if (pendingSubmission) {
        await recoverSubmission();
      } else {
        showError(error.message, true);
        submitting = false;
        submitConfirm.disabled = false;
        submitConfirm.textContent = '提交并锁定';
        setAnswerControlsDisabled(false);
        setSubmitDismissDisabled(false);
        submitBtn.disabled = false;
        form.removeAttribute('aria-busy');
      }
    }
  });

  skipBtn?.addEventListener('click', async () => {
    if (submissionOutcomeUnknown) {
      await recoverSubmission();
      return;
    }
    if (submitting) return;
    if (pendingSubmission && !await returnToAnswer()) return;
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
    if (submissionOutcomeUnknown) {
      await recoverSubmission();
      return;
    }
    if (submitting) return;
    if (pendingSubmission && !await returnToAnswer()) return;
    clearError();
    try {
      if (submitBtn) await saveDraft(true);
      await closeProcessAttempt('navigation', Number(link.dataset.assignmentId));
      window.location.href = link.href;
    } catch (error) {
      showError('草稿保存失败，尚未切换题目。请检查网络后重试。', true);
    }
  }));

  restore();
  lastRecordedResponseState = initialResponseState;
  lastSerialized = JSON.stringify(collect());
  lastQueuedSerialized = lastSerialized;
  if (!choiceQuestion) lastStableText = String(collect() ?? '');
  const restoreGeneration = confirmationGeneration;
  processReady.then(async () => {
    if (!processOutbox?.restoreSubmissionIntent) {
      submissionIntentLoaded = true;
      if (submitBtn) submitBtn.disabled = false;
      return;
    }
    try {
      const intent = await processOutbox.restoreSubmissionIntent(
        Number(form.dataset.processSessionId),
        Number(form.dataset.processAttemptId),
      );
      submissionIntentLoaded = true;
      if (!intent) {
        if (submitBtn) submitBtn.disabled = false;
        return;
      }
      if (restoreGeneration !== confirmationGeneration) return;
      pendingSubmission = intent;
      confirmationAnswer = intent.answer;
      submissionConfidence = intent.confidence || null;
      await recoverSubmission();
    } catch (_error) {
      submissionIntentLoaded = true;
      showError('本地提交记录读取失败，请刷新页面后重试。', true);
    }
  });
  const finishForm = document.querySelector('[data-finish-form]');
  finishForm?.addEventListener('assessment:finish-requested', async (event) => {
    event.preventDefault();
    if (submissionOutcomeUnknown) {
      await recoverSubmission();
      return;
    }
    if (submitting) return;
    if (pendingSubmission && !await returnToAnswer()) return;
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
    confidenceTouched = true;
    confidenceSubmit.disabled = false;
    confidenceValue.textContent = `${confidenceInput.value}%`;
  });

  confidenceSubmit?.addEventListener('click', () => resolveConfidence(false));
  confidenceSkip?.addEventListener('click', () => resolveConfidence(true));

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
