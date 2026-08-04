# Question Bank Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add direct question-bank navigation, owner-only inline visibility switching, concise displayed question labels, and downloadable CSV import examples.

**Architecture:** Keep the Django monolith and existing routes intact while adding one owner-scoped POST endpoint for visibility changes and two role-protected GET endpoints for fixed fixture downloads. Render the inline control with native `<details>`, a normal POST form, and a small JavaScript enhancement for automatic submission; preserve stored question codes and change only their list-page presentation.

**Tech Stack:** Django 5.2, Django Templates, Bootstrap 5.3, native CSS, native JavaScript, Django `TestCase`.

---

## File map

- Modify `apps/question_bank/forms.py`: define the visibility-only model form.
- Modify `apps/question_bank/views.py`: handle visibility updates and fixed CSV fixture downloads.
- Modify `apps/question_bank/urls.py`: expose one POST update route and two GET download routes.
- Modify `apps/question_bank/tests.py`: cover permissions, validation, downloads, navigation links, and question-label rendering.
- Modify `templates/teacher/bank_list.html`: link bank names/counts and render the visibility disclosure form.
- Modify `templates/teacher/bank_detail.html`: display `Q{{ sequence }}` without changing persisted codes.
- Modify `templates/teacher/import_questions.html`: link the question CSV example.
- Modify `templates/teacher/import_kcs.html`: link the KC CSV example.
- Modify `static/js/app.js`: focus the revealed selector and auto-submit a changed value.
- Modify `static/css/app.css`: style the disclosure control using existing tokens and preserve visible focus.
- Modify `tests/test_ui_contracts.py`: protect the native disclosure and auto-submit DOM contract.

### Task 1: Owner-scoped visibility update

**Files:**
- Modify: `apps/question_bank/forms.py`
- Modify: `apps/question_bank/views.py`
- Modify: `apps/question_bank/urls.py`
- Test: `apps/question_bank/tests.py`

- [ ] **Step 1: Write failing visibility endpoint tests**

Add these imports and tests to `apps/question_bank/tests.py`:

```python
from django.urls import reverse


class QuestionBankVisibilityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", role=User.Role.TEACHER)
        self.other_teacher = User.objects.create_user(username="other", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.owner, name="Research bank")
        self.url = reverse("question_bank:bank_visibility_update", args=[self.bank.pk])

    def test_owner_can_update_visibility(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url, {"visibility": QuestionBank.Visibility.SHARED})

        self.assertRedirects(response, reverse("question_bank:bank_list"))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.SHARED)

    def test_non_owner_cannot_update_visibility(self):
        self.client.force_login(self.other_teacher)

        response = self.client.post(self.url, {"visibility": QuestionBank.Visibility.SHARED})

        self.assertEqual(response.status_code, 404)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)

    def test_invalid_visibility_does_not_update_bank(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url, {"visibility": "public"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "可见性更新失败，请重新选择")
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.visibility, QuestionBank.Visibility.PRIVATE)

    def test_visibility_endpoint_rejects_get(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python manage.py test apps.question_bank.tests.QuestionBankVisibilityTests
```

Expected: errors while reversing `question_bank:bank_visibility_update`, because the route does not exist.

- [ ] **Step 3: Add the minimal visibility form and endpoint**

Add to `apps/question_bank/forms.py`:

```python
class QuestionBankVisibilityForm(forms.ModelForm):
    class Meta:
        model = QuestionBank
        fields = ["visibility"]
```

Add the method decorator import and view to `apps/question_bank/views.py`:

```python
from django.views.decorators.http import require_POST
from .forms import QuestionBankForm, QuestionBankVisibilityForm, QuestionEditForm, UploadForm


@role_required("teacher", "admin")
@require_POST
def bank_visibility_update(request, pk):
    bank = _owned_bank(request, pk)
    form = QuestionBankVisibilityForm(request.POST, instance=bank)
    if form.is_valid():
        form.save()
        messages.success(request, f"题库“{bank.name}”的可见性已更新")
    else:
        messages.error(request, "可见性更新失败，请重新选择")
    return redirect("question_bank:bank_list")
```

Add to `apps/question_bank/urls.py` immediately after the bank detail route:

```python
path("banks/<int:pk>/visibility/", views.bank_visibility_update, name="bank_visibility_update"),
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run:

```powershell
python manage.py test apps.question_bank.tests.QuestionBankVisibilityTests
```

Expected: four tests pass.

- [ ] **Step 5: Commit the endpoint**

```powershell
git add apps/question_bank/forms.py apps/question_bank/views.py apps/question_bank/urls.py apps/question_bank/tests.py
git commit -m "feat: add question bank visibility updates"
```

### Task 2: Direct navigation and inline visibility UI

**Files:**
- Modify: `apps/question_bank/views.py`
- Modify: `templates/teacher/bank_list.html`
- Modify: `static/js/app.js`
- Modify: `static/css/app.css`
- Test: `apps/question_bank/tests.py`
- Test: `tests/test_ui_contracts.py`

- [ ] **Step 1: Write failing rendered-page and DOM-contract tests**

Add to `apps/question_bank/tests.py`:

```python
class QuestionBankListUITests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="list-owner", role=User.Role.TEACHER)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="Clickable bank")
        Question.objects.create(
            bank=self.bank,
            sequence=1,
            text="2 + 2?",
            question_type=Question.Type.NUMERIC,
            correct_answer="4",
        )
        self.client.force_login(self.teacher)

    def test_bank_name_and_question_count_link_to_detail(self):
        response = self.client.get(reverse("question_bank:bank_list"))
        detail_url = reverse("question_bank:bank_detail", args=[self.bank.pk])

        self.assertContains(response, f'<a class="cell-title" href="{detail_url}">Clickable bank</a>', html=True)
        self.assertContains(response, f'<a class="count-link" href="{detail_url}" aria-label="进入题库 Clickable bank，共 1 题">1</a>', html=True)

    def test_bank_list_renders_owner_scoped_visibility_form(self):
        response = self.client.get(reverse("question_bank:bank_list"))

        self.assertContains(response, 'data-visibility-control')
        self.assertContains(response, 'data-auto-submit')
        self.assertContains(
            response,
            reverse("question_bank:bank_visibility_update", args=[self.bank.pk]),
        )
        for value, label in QuestionBank.Visibility.choices:
            self.assertContains(response, f'<option value="{value}"', html=False)
            self.assertContains(response, label)
```

Add to `tests/test_ui_contracts.py`:

```python
    def test_bank_visibility_control_has_native_and_enhanced_paths(self):
        template = (ROOT / "templates" / "teacher" / "bank_list.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn("<details", template)
        self.assertIn("<summary", template)
        self.assertIn("<noscript>", template)
        self.assertIn("data-visibility-control", template)
        self.assertIn("data-auto-submit", template)
        self.assertIn("form.requestSubmit()", script)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python manage.py test apps.question_bank.tests.QuestionBankListUITests tests.test_ui_contracts.UIContractTests.test_bank_visibility_control_has_native_and_enhanced_paths
```

Expected: failures because the list has plain text/counts, no disclosure form, and no auto-submit listener.

- [ ] **Step 3: Pass visibility choices to the list template**

Change the `bank_list` render call in `apps/question_bank/views.py` to:

```python
return render(request, "teacher/bank_list.html", {
    "banks": banks,
    "shared_banks": shared_banks,
    "form": form,
    "visibility_choices": QuestionBank.Visibility.choices,
})
```

- [ ] **Step 4: Render direct links and the native visibility disclosure**

Replace the owned-bank row in `templates/teacher/bank_list.html` with:

```django
<tr>
  <td>
    <a class="cell-title" href="{% url 'question_bank:bank_detail' bank.pk %}">{{ bank.name }}</a>
    <div class="cell-meta">所有者：{{ bank.owner.shown_name }}<br>{{ bank.description|default:'未填写说明'|truncatechars:72 }}</div>
  </td>
  <td class="numeric">
    <a class="count-link" href="{% url 'question_bank:bank_detail' bank.pk %}" aria-label="进入题库 {{ bank.name }}，共 {{ bank.questions.count }} 题">{{ bank.questions.count }}</a>
  </td>
  <td>
    <details class="visibility-control" data-visibility-control>
      <summary class="visibility-summary">
        <span class="status-badge {% if bank.visibility == 'shared' %}status-info{% else %}status-neutral{% endif %}">{{ bank.get_visibility_display }}</span>
      </summary>
      <form class="visibility-form" method="post" action="{% url 'question_bank:bank_visibility_update' bank.pk %}">
        {% csrf_token %}
        <label class="visually-hidden" for="visibility-{{ bank.pk }}">修改 {{ bank.name }} 的可见性</label>
        <select class="form-select form-select-sm" id="visibility-{{ bank.pk }}" name="visibility" data-auto-submit>
          {% for value, label in visibility_choices %}
            <option value="{{ value }}"{% if bank.visibility == value %} selected{% endif %}>{{ label }}</option>
          {% endfor %}
        </select>
        <noscript><button class="btn btn-sm btn-primary mt-2" type="submit">保存</button></noscript>
      </form>
    </details>
  </td>
  <td class="numeric text-nowrap">{{ bank.updated_at|date:'Y-m-d H:i' }}</td>
  <td class="text-end"><a class="btn btn-sm btn-outline-primary" href="{% url 'question_bank:bank_detail' bank.pk %}">管理</a></td>
</tr>
```

- [ ] **Step 5: Add focused auto-submit enhancement**

Inside the `DOMContentLoaded` callback in `static/js/app.js`, add:

```javascript
    document.querySelectorAll('[data-visibility-control]').forEach((control) => {
      const select = control.querySelector('[data-auto-submit]');
      control.addEventListener('toggle', () => {
        if (control.open) select?.focus();
      });
    });

    document.querySelectorAll('[data-auto-submit]').forEach((control) => {
      control.addEventListener('change', () => {
        const form = control.form;
        if (!form) return;
        form.setAttribute('aria-busy', 'true');
        form.requestSubmit();
      });
    });
```

- [ ] **Step 6: Style the control with existing tokens**

Add near the table and status styles in `static/css/app.css`:

```css
.count-link { color: var(--kt-link); font-weight: var(--weight-semibold); text-underline-offset: 0.18em; }
.visibility-control { min-width: 7rem; }
.visibility-summary { width: fit-content; cursor: pointer; list-style: none; }
.visibility-summary::-webkit-details-marker { display: none; }
.visibility-summary::after { margin-left: var(--space-1); color: var(--kt-muted); content: "▾"; font-size: var(--font-size-xs); }
.visibility-control[open] .visibility-summary::after { content: "▴"; }
.visibility-form { max-width: 9rem; margin-top: var(--space-2); }
```

- [ ] **Step 7: Run the tests and verify GREEN**

Run:

```powershell
python manage.py test apps.question_bank.tests.QuestionBankListUITests tests.test_ui_contracts.UIContractTests.test_bank_visibility_control_has_native_and_enhanced_paths
node --check static/js/app.js
```

Expected: all targeted tests pass and Node reports no syntax error.

- [ ] **Step 8: Commit the list interaction**

```powershell
git add apps/question_bank/views.py apps/question_bank/tests.py templates/teacher/bank_list.html static/js/app.js static/css/app.css tests/test_ui_contracts.py
git commit -m "feat: improve question bank list interactions"
```

### Task 3: Protected CSV example downloads

**Files:**
- Modify: `apps/question_bank/views.py`
- Modify: `apps/question_bank/urls.py`
- Modify: `templates/teacher/import_questions.html`
- Modify: `templates/teacher/import_kcs.html`
- Test: `apps/question_bank/tests.py`

- [ ] **Step 1: Write failing download tests**

Add to `apps/question_bank/tests.py`:

```python
class ImportTemplateDownloadTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="template-teacher", role=User.Role.TEACHER)
        self.client.force_login(self.teacher)

    def test_question_template_download_uses_fixture(self):
        response = self.client.get(reverse("question_bank:question_import_template"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn('filename="question_import_template.csv"', response["Content-Disposition"])
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("序号,题目,选项A,选项B,选项C,选项D,选项E,选项F,答案", content)
        self.assertIn("Knowledge Tracing 的中文名称是什么？", content)

    def test_kc_template_download_uses_fixture(self):
        response = self.client.get(reverse("question_bank:kc_import_template"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn('filename="kc_mapping_template.csv"', response["Content-Disposition"])
        content = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("题目序号,主知识点,次知识点", content)
        self.assertIn("基础运算,整数加法", content)

    def test_anonymous_user_must_log_in_to_download_template(self):
        self.client.logout()

        response = self.client.get(reverse("question_bank:question_import_template"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python manage.py test apps.question_bank.tests.ImportTemplateDownloadTests
```

Expected: URL reversing errors because both download routes are absent.

- [ ] **Step 3: Add a fixed-fixture download helper and views**

Add imports and code to `apps/question_bank/views.py`:

```python
from django.conf import settings
from django.http import FileResponse, Http404


def _fixture_download(filename):
    path = settings.BASE_DIR / "fixtures" / filename
    if not path.is_file():
        raise Http404("示例文件不存在")
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type="text/csv; charset=utf-8",
    )


@role_required("teacher", "admin")
def question_import_template(request):
    return _fixture_download("question_import_template.csv")


@role_required("teacher", "admin")
def kc_import_template(request):
    return _fixture_download("kc_mapping_template.csv")
```

Add to `apps/question_bank/urls.py`:

```python
path("import-templates/questions.csv", views.question_import_template, name="question_import_template"),
path("import-templates/kcs.csv", views.kc_import_template, name="kc_import_template"),
```

- [ ] **Step 4: Run download tests and verify GREEN**

Run:

```powershell
python manage.py test apps.question_bank.tests.ImportTemplateDownloadTests
```

Expected: three tests pass.

- [ ] **Step 5: Write failing import-page link tests**

Add to `ImportTemplateDownloadTests`:

```python
    def test_import_pages_link_to_matching_templates(self):
        bank = QuestionBank.objects.create(owner=self.teacher, name="Import bank")

        question_page = self.client.get(reverse("question_bank:question_import", args=[bank.pk]))
        kc_page = self.client.get(reverse("question_bank:kc_import", args=[bank.pk]))

        self.assertContains(question_page, reverse("question_bank:question_import_template"))
        self.assertContains(question_page, "下载题目 CSV 示例")
        self.assertContains(kc_page, reverse("question_bank:kc_import_template"))
        self.assertContains(kc_page, "下载 KC CSV 示例")
```

- [ ] **Step 6: Run the link test and verify RED**

Run:

```powershell
python manage.py test apps.question_bank.tests.ImportTemplateDownloadTests.test_import_pages_link_to_matching_templates
```

Expected: failure because neither import page contains a download link.

- [ ] **Step 7: Add matching links to both import notices**

In `templates/teacher/import_questions.html`, add within the template-column notice:

```django
<a class="btn btn-sm btn-outline-primary ms-md-2" href="{% url 'question_bank:question_import_template' %}">下载题目 CSV 示例</a>
```

In `templates/teacher/import_kcs.html`, add within the template-column notice:

```django
<a class="btn btn-sm btn-outline-primary ms-md-2" href="{% url 'question_bank:kc_import_template' %}">下载 KC CSV 示例</a>
```

- [ ] **Step 8: Run the complete download test class**

Run:

```powershell
python manage.py test apps.question_bank.tests.ImportTemplateDownloadTests
```

Expected: four tests pass.

- [ ] **Step 9: Commit the downloads**

```powershell
git add apps/question_bank/views.py apps/question_bank/urls.py apps/question_bank/tests.py templates/teacher/import_questions.html templates/teacher/import_kcs.html
git commit -m "feat: add import template downloads"
```

### Task 4: Concise displayed question labels

**Files:**
- Modify: `templates/teacher/bank_detail.html`
- Test: `apps/question_bank/tests.py`

- [ ] **Step 1: Write the failing display-only label test**

Add to `apps/question_bank/tests.py`:

```python
class QuestionLabelDisplayTests(TestCase):
    def test_bank_detail_displays_short_label_without_mutating_code(self):
        teacher = User.objects.create_user(username="label-teacher", role=User.Role.TEACHER)
        bank = QuestionBank.objects.create(owner=teacher, name="Label bank")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            text="Displayed question",
            question_type=Question.Type.SHORT,
            correct_answer="answer",
        )
        self.client.force_login(teacher)

        response = self.client.get(reverse("question_bank:bank_detail", args=[bank.pk]))

        self.assertContains(response, '<div class="cell-meta mono">Q1</div>', html=True)
        self.assertNotContains(response, '<div class="cell-meta mono">Q0001</div>', html=True)
        question.refresh_from_db()
        self.assertEqual(question.code, "Q0001")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python manage.py test apps.question_bank.tests.QuestionLabelDisplayTests
```

Expected: failure because the page currently renders `q.code` as `Q0001`.

- [ ] **Step 3: Change only the rendered auxiliary label**

In `templates/teacher/bank_detail.html`, replace:

```django
<div class="cell-meta mono">{{ q.code }}</div>
```

with:

```django
<div class="cell-meta mono">Q{{ q.sequence }}</div>
```

- [ ] **Step 4: Run the test and verify GREEN**

Run:

```powershell
python manage.py test apps.question_bank.tests.QuestionLabelDisplayTests
```

Expected: the test passes and confirms the stored code remains `Q0001`.

- [ ] **Step 5: Commit the presentation change**

```powershell
git add apps/question_bank/tests.py templates/teacher/bank_detail.html
git commit -m "feat: simplify displayed question labels"
```

### Task 5: Full verification and visual acceptance

**Files:**
- Modify only if verification exposes a defect in the files already listed.

- [ ] **Step 1: Run the complete automated gate**

Run:

```powershell
python manage.py check
python manage.py test
python manage.py collectstatic --noinput
node --check static/js/app.js
node --check static/js/assessment.js
```

Expected: Django reports no issues, all tests pass, static collection completes, and both Node syntax checks exit successfully.

- [ ] **Step 2: Check the desktop teacher flow at 1440 × 900**

Using the seeded teacher account, exercise these routes:

```text
/teacher/banks/
/teacher/banks/<owned-bank-id>/
/teacher/banks/<owned-bank-id>/import/
/teacher/banks/<owned-bank-id>/kc-import/
```

Verify the name/count links, disclosure expansion, immediate visibility update and message, `Q1` labels, both downloads, keyboard focus, and absence of page-level horizontal overflow.

- [ ] **Step 3: Check the mobile teacher flow at 390 × 844**

Exercise the same routes and verify the table scroll container contains overflow, the visibility selector remains reachable, both download links wrap without clipping, and keyboard-visible state does not depend on color.

- [ ] **Step 4: Confirm the final diff is scoped and clean**

Run:

```powershell
git diff --check
git status --short
git log -5 --oneline
```

Expected: no whitespace errors; only intentional verification artifacts, if any, remain uncommitted.

- [ ] **Step 5: Commit any verification repair**

If a defect was found and repaired through a new failing regression test, stage only the affected files and commit with:

```powershell
git commit -m "fix: resolve question bank verification findings"
```

If no repair was needed, do not create an empty commit.
