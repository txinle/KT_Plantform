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

    document.querySelectorAll('[data-visibility-control]').forEach((control) => {
      control.addEventListener('toggle', () => {
        if (control.open) control.querySelector('[data-auto-submit]')?.focus();
      });
    });

    document.querySelectorAll('[data-auto-submit]').forEach((field) => {
      field.addEventListener('change', () => {
        const form = field.closest('form');
        if (!form) return;
        form.setAttribute('aria-busy', 'true');
        form.requestSubmit();
      });
    });

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

    const conceptPicker = document.querySelector('[data-concept-picker]');
    const enhanceConceptSelect = (select) => {
      if (!conceptPicker || !select) return;
      const mode = select.dataset.conceptSelect;
      const field = select.closest('.form-field');
      const label = field?.querySelector('label');
      const searchUrl = conceptPicker.dataset.searchUrl;
      if (!field || !label || !searchUrl) return;

      const wrapper = document.createElement('div');
      wrapper.className = 'concept-combobox';
      const input = document.createElement('input');
      input.className = 'form-control';
      input.id = `${select.id}_search`;
      input.type = 'search';
      input.autocomplete = 'off';
      input.placeholder = mode === 'primary' ? '输入名称搜索主 KC' : '输入名称添加次 KC';
      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-expanded', 'false');
      const listId = `${select.id}_results`;
      input.setAttribute('aria-controls', listId);
      const list = document.createElement('ul');
      list.className = 'concept-results';
      list.id = listId;
      list.setAttribute('role', 'listbox');
      list.hidden = true;
      const status = document.createElement('div');
      status.className = 'visually-hidden';
      status.setAttribute('aria-live', 'polite');
      const tags = document.createElement('div');
      tags.className = 'concept-tags';

      wrapper.append(input, list, status);
      if (mode === 'secondary') wrapper.append(tags);
      select.before(wrapper);
      select.hidden = true;
      label.htmlFor = input.id;
      const describedBy = select.getAttribute('aria-describedby');
      if (describedBy) input.setAttribute('aria-describedby', describedBy);
      if (select.getAttribute('aria-invalid') === 'true') input.setAttribute('aria-invalid', 'true');

      let results = [];
      let activeIndex = -1;
      let searchTimer = null;
      let controller = null;

      const selectedOptions = () => Array.from(select.selectedOptions).filter((option) => option.value);
      const closeResults = () => {
        list.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        activeIndex = -1;
      };
      const updateActive = (nextIndex) => {
        const options = Array.from(list.querySelectorAll('[role="option"]'));
        if (!options.length) return;
        activeIndex = (nextIndex + options.length) % options.length;
        options.forEach((option, index) => option.setAttribute('aria-selected', String(index === activeIndex)));
        const active = options[activeIndex];
        input.setAttribute('aria-activedescendant', active.id);
        active.scrollIntoView({ block: 'nearest' });
      };
      const renderTags = () => {
        if (mode !== 'secondary') return;
        tags.replaceChildren();
        selectedOptions().forEach((option) => {
          const tag = document.createElement('span');
          tag.className = 'concept-tag';
          const text = document.createElement('span');
          text.textContent = option.textContent;
          const remove = document.createElement('button');
          remove.className = 'concept-tag-remove';
          remove.type = 'button';
          remove.setAttribute('aria-label', `移除次 KC ${option.textContent}`);
          remove.textContent = '×';
          remove.addEventListener('click', () => {
            option.selected = false;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            renderTags();
            input.focus();
          });
          tag.append(text, remove);
          tags.append(tag);
        });
      };
      const chooseResult = (item) => {
        let option = Array.from(select.options).find((candidate) => candidate.value === String(item.id));
        if (!option) {
          option = new Option(item.name, item.id);
          select.add(option);
        }
        if (mode === 'primary') {
          select.value = String(item.id);
          input.value = item.name;
        } else {
          option.selected = true;
          input.value = '';
          renderTags();
        }
        select.dispatchEvent(new Event('change', { bubbles: true }));
        closeResults();
        input.focus();
      };
      const renderResults = (items) => {
        const selectedIds = new Set(selectedOptions().map((option) => option.value));
        results = mode === 'secondary'
          ? items.filter((item) => !selectedIds.has(String(item.id)))
          : items;
        list.replaceChildren();
        results.forEach((item, index) => {
          const option = document.createElement('li');
          option.id = `${listId}_${index}`;
          option.className = 'concept-result';
          option.setAttribute('role', 'option');
          option.setAttribute('aria-selected', 'false');
          option.textContent = item.name;
          option.addEventListener('mousedown', (event) => event.preventDefault());
          option.addEventListener('click', () => chooseResult(item));
          list.append(option);
        });
        status.textContent = results.length ? `找到 ${results.length} 个 KC` : '没有匹配的课程 KC';
        if (!results.length) {
          closeResults();
          return;
        }
        list.hidden = false;
        input.setAttribute('aria-expanded', 'true');
        updateActive(0);
      };
      const search = async () => {
        const query = input.value.trim();
        if (!query) {
          closeResults();
          return;
        }
        controller?.abort();
        controller = new AbortController();
        try {
          const response = await fetch(`${searchUrl}?q=${encodeURIComponent(query)}`, {
            headers: { Accept: 'application/json' },
            signal: controller.signal,
          });
          if (!response.ok) throw new Error('KC search failed');
          const payload = await response.json();
          renderResults(payload.results || []);
        } catch (error) {
          if (error.name === 'AbortError') return;
          closeResults();
          status.textContent = 'KC 联想暂时不可用，请使用原选择框或稍后重试';
        }
      };

      input.addEventListener('input', () => {
        if (mode === 'primary') select.value = '';
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(search, 160);
      });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') {
          event.preventDefault();
          updateActive(activeIndex + 1);
        } else if (event.key === 'ArrowUp') {
          event.preventDefault();
          updateActive(activeIndex - 1);
        } else if (event.key === 'Enter' && !list.hidden && activeIndex >= 0) {
          event.preventDefault();
          chooseResult(results[activeIndex]);
        } else if (event.key === 'Escape') {
          closeResults();
        }
      });
      input.addEventListener('blur', () => window.setTimeout(closeResults, 120));

      if (mode === 'primary') input.value = selectedOptions()[0]?.textContent || '';
      renderTags();
    };
    document.querySelectorAll('[data-concept-select]').forEach(enhanceConceptSelect);

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

    const selectionMode = document.querySelector('[data-selection-mode-controller]');
    const quotaFields = document.querySelector('[data-selection-quota-fields]');
    const quotaInputs = Array.from(document.querySelectorAll('[data-quota-input]'));
    const quotaTotal = document.querySelector('[data-quota-total]');
    const updateQuotaFields = () => {
      if (!selectionMode || !quotaFields) return;
      const active = selectionMode.value === 'quota';
      quotaFields.hidden = !active;
      quotaInputs.forEach((field) => { field.disabled = !active; });
      const total = quotaInputs.reduce((sum, field) => sum + Math.max(0, Number(field.value) || 0), 0);
      if (quotaTotal) quotaTotal.textContent = String(total);
    };
    selectionMode?.addEventListener('change', updateQuotaFields);
    quotaInputs.forEach((field) => field.addEventListener('input', updateQuotaFields));
    updateQuotaFields();

    document.querySelectorAll('[data-participant-rules]').forEach((form) => {
      form.querySelectorAll('[data-participant-class]').forEach((classRow) => {
        const classInput = classRow.querySelector('[data-participant-class-input]');
        const includeControls = classRow.querySelectorAll('[data-participant-include]');
        const excludeControls = classRow.querySelectorAll('[data-participant-exclude]');
        if (!classInput) return;

        const updateParticipantControls = () => {
          const selected = classInput.checked;
          includeControls.forEach((control) => {
            control.hidden = selected;
            control.querySelectorAll('input').forEach((input) => {
              input.disabled = selected;
            });
          });
          excludeControls.forEach((control) => {
            control.hidden = !selected;
            control.querySelectorAll('input').forEach((input) => {
              input.disabled = !selected;
            });
          });
        };

        classInput.addEventListener('change', updateParticipantControls);
        updateParticipantControls();
      });
    });

    const revisionRoot = document.querySelector('[data-course-revision-url]');
    if (revisionRoot) {
      let courseRevision = revisionRoot.dataset.courseRevision;
      let coursePageDirty = false;
      let coursePageSubmitting = false;
      let refreshNotice = null;

      document.querySelectorAll('form').forEach((form) => {
        form.addEventListener('input', () => { coursePageDirty = true; });
        form.addEventListener('change', () => { coursePageDirty = true; });
        form.addEventListener('submit', () => { coursePageSubmitting = true; });
      });

      const showRefreshNotice = () => {
        if (refreshNotice) return;
        refreshNotice = document.createElement('div');
        refreshNotice.className = 'alert alert-warning course-refresh-notice';
        refreshNotice.setAttribute('role', 'status');
        refreshNotice.innerHTML = '<span>其他课程管理者已更新数据。当前表单有未保存内容，请保存后刷新。</span>';
        const reloadButton = document.createElement('button');
        reloadButton.className = 'btn btn-sm btn-outline-secondary ms-3';
        reloadButton.type = 'button';
        reloadButton.textContent = '立即刷新';
        reloadButton.addEventListener('click', () => window.location.reload());
        refreshNotice.append(reloadButton);
        revisionRoot.prepend(refreshNotice);
      };

      const checkCourseRevision = async () => {
        if (document.hidden || coursePageSubmitting) return;
        try {
          const response = await fetch(revisionRoot.dataset.courseRevisionUrl, {
            headers: { Accept: 'application/json' },
            cache: 'no-store',
            credentials: 'same-origin',
          });
          if (response.status === 404 || response.status === 403) {
            window.location.reload();
            return;
          }
          if (!response.ok) return;
          const { revision } = await response.json();
          if (revision !== courseRevision) {
            if (coursePageDirty) showRefreshNotice();
            else window.location.reload();
          }
        } catch (_error) {
          // Network interruptions are retried by the next polling interval.
        }
      };

      checkCourseRevision();
      window.setInterval(checkCourseRevision, 4000);
    }
  });
})();
