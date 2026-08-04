(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]), select, textarea').forEach((element) => {
      if (!element.classList.contains('form-control') && !element.classList.contains('form-select')) {
        element.classList.add(element.tagName === 'SELECT' ? 'form-select' : 'form-control');
      }
      const error = document.getElementById(`${element.id}_error`);
      const help = document.getElementById(`${element.id}_help`);
      const descriptions = [help?.id, error?.id].filter(Boolean);
      if (descriptions.length) element.setAttribute('aria-describedby', descriptions.join(' '));
      if (error) element.setAttribute('aria-invalid', 'true');
    });

    document.querySelectorAll('input[type="checkbox"], input[type="radio"]').forEach((element) => {
      element.classList.add('form-check-input');
    });

    const errorSummary = document.querySelector('[data-error-summary]');
    if (errorSummary) errorSummary.focus();

    const finishDialog = document.getElementById('finish-confirm-dialog');
    const finishForm = document.querySelector('[data-finish-form]');
    const finishConfirm = finishDialog?.querySelector('[data-confirm-action]');
    finishConfirm?.addEventListener('click', () => {
      finishConfirm.disabled = true;
      finishConfirm.textContent = '正在结束';
      finishForm?.submit();
    });

    document.querySelectorAll('[data-confirm-form]').forEach((button) => {
      const dialog = document.querySelector(button.dataset.confirmForm);
      const form = button.closest('form');
      const confirmButton = dialog?.querySelector('[data-confirm-action]');
      if (!dialog || !form || !confirmButton) return;
      confirmButton.addEventListener('click', () => {
        confirmButton.disabled = true;
        confirmButton.textContent = confirmButton.dataset.loadingLabel || '正在处理';
        form.submit();
      }, { once: true });
    });

    const questionType = document.querySelector('[data-question-type-controller]');
    const updateQuestionFields = () => {
      if (!questionType) return;
      const value = questionType.value;
      document.querySelectorAll('[data-question-field-group]').forEach((group) => {
        const allowed = group.dataset.questionFieldGroup.split(' ');
        const active = allowed.includes(value);
        group.hidden = !active;
        group.querySelectorAll('input, select, textarea').forEach((field) => {
          field.disabled = !active;
        });
      });
    };
    questionType?.addEventListener('change', updateQuestionFields);
    updateQuestionFields();

    const timeMode = document.querySelector('[data-time-mode-controller]');
    const updateTimeFields = () => {
      if (!timeMode) return;
      document.querySelectorAll('[data-time-field]').forEach((group) => {
        const active = group.dataset.timeField === timeMode.value;
        group.hidden = !active;
        group.querySelectorAll('input').forEach((field) => { field.disabled = !active; });
      });
    };
    timeMode?.addEventListener('change', updateTimeFields);
    updateTimeFields();
  });
})();
