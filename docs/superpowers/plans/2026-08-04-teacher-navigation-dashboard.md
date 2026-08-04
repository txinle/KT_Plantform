# Teacher Navigation and Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the teacher workspace hierarchy visible, show name plus login account for teachers and students, and add class/student management to the teacher dashboard workflow.

**Architecture:** Keep the existing Django Templates, Bootstrap, and vanilla CSS stack. `base.html` owns the shared account identity, primary teacher module navigation, and breadcrumb shell; each teacher template supplies explicit breadcrumb items so hierarchy never depends on parsing URL strings. The dashboard presents six existing routes in three research-workflow groups without changing views, models, or data semantics.

**Tech Stack:** Django 5.2 templates and test client, Bootstrap 5.3.8, vanilla CSS, Python `unittest`, Git.

---

## File map

- Modify `apps/accounts/tests.py`: rendered teacher/student header and breadcrumb behavior.
- Modify `tests/test_ui_contracts.py`: source-level contracts for desktop navigation, teacher breadcrumb coverage, and dashboard participant links.
- Modify `templates/base.html`: shared account identity and breadcrumb shell.
- Modify all files in `templates/teacher/`: explicit hierarchy items; dashboard workflow grouping; remove redundant back links.
- Modify `static/css/app.css`: desktop navigation specificity, breadcrumb layout, identity treatment, and workflow stages.
- Modify `docs/ui/UI-CHANGELOG.md`: record the follow-up changes and remaining visual verification boundary.
- Regenerate `SHA256SUMS.txt`: update the mechanically derived package integrity manifest.

### Task 1: Unified teacher and student account identity

**Files:**
- Modify: `apps/accounts/tests.py`
- Modify: `templates/base.html`

- [ ] **Step 1: Write the failing rendered-response tests**

Append this test class to `apps/accounts/tests.py`:

```python
class HeaderIdentityTests(TestCase):
    def test_teacher_header_shows_name_and_login_account(self):
        teacher = User.objects.create_user(
            username="teacher-account",
            password="x",
            role=User.Role.TEACHER,
            display_name="陈老师",
        )
        self.client.force_login(teacher)

        response = self.client.get(reverse("question_bank:dashboard"))

        self.assertContains(response, '<span class="account-name">陈老师</span>', html=True)
        self.assertContains(response, '<span class="account-username mono">teacher-account</span>', html=True)
        self.assertNotContains(response, "陈老师 / 教师")

    def test_student_header_uses_the_same_name_and_account_format(self):
        student = User.objects.create_user(
            username="S101",
            password="x",
            role=User.Role.STUDENT,
            student_number="S101",
            display_name="林同学",
        )
        self.client.force_login(student)

        response = self.client.get(reverse("assessment:dashboard"))

        self.assertContains(response, '<span class="account-name">林同学</span>', html=True)
        self.assertContains(response, '<span class="account-username mono">S101</span>', html=True)
        self.assertNotContains(response, "林同学 / 学生")

    def test_header_does_not_repeat_account_when_name_falls_back_to_username(self):
        teacher = User.objects.create_user(
            username="fallback-account",
            password="x",
            role=User.Role.TEACHER,
        )
        self.client.force_login(teacher)

        response = self.client.get(reverse("question_bank:dashboard"))
        account_label = response.content.decode("utf-8").split('class="account-label"', 1)[1].split("</span>", 1)[0]

        self.assertEqual(account_label.count("fallback-account"), 1)
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run:

```powershell
python manage.py test apps.accounts.tests.HeaderIdentityTests
```

Expected: FAIL because `base.html` currently renders `shown_name / get_role_display` and has no `account-name` or `account-username` elements.

- [ ] **Step 3: Implement the shared identity format**

Replace the current `.account-label` span in `templates/base.html` with:

```django
<span class="account-label d-none d-sm-inline">
  {% if user.shown_name != user.username %}
    <span class="account-name">{{ user.shown_name }}</span>
    <span class="account-separator" aria-hidden="true">·</span>
  {% endif %}
  <span class="account-username mono">{{ user.username }}</span>
</span>
```

- [ ] **Step 4: Run the focused tests and verify green**

Run:

```powershell
python manage.py test apps.accounts.tests.HeaderIdentityTests
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit the focused change**

```powershell
git add apps/accounts/tests.py templates/base.html
git commit -m "feat: show account identity consistently"
```

### Task 2: Restore desktop teacher navigation and add the breadcrumb shell

**Files:**
- Modify: `tests/test_ui_contracts.py`
- Modify: `templates/base.html`
- Modify: `static/css/app.css`

- [ ] **Step 1: Write failing source-contract tests**

Add these methods to `UIContractTests` in `tests/test_ui_contracts.py`:

```python
def test_teacher_navigation_remains_visible_on_desktop(self):
    source = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    self.assertIn(".teacher-nav .teacher-nav-collapse { display: block; }", source)
    self.assertIn(".teacher-nav-collapse:not(.show) { display: none; }", source)

def test_base_defines_accessible_teacher_breadcrumb_shell(self):
    source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    self.assertIn('{% block breadcrumb_items %}', source)
    self.assertIn('aria-label="层级目录"', source)
    self.assertIn('aria-current="page"', source)
```

- [ ] **Step 2: Run the contracts and verify red**

Run:

```powershell
python -m unittest tests.test_ui_contracts.UIContractTests.test_teacher_navigation_remains_visible_on_desktop tests.test_ui_contracts.UIContractTests.test_base_defines_accessible_teacher_breadcrumb_shell
```

Expected: 2 failures because the specific desktop override and breadcrumb shell do not exist.

- [ ] **Step 3: Add the breadcrumb shell to `templates/base.html`**

Insert this block at the start of `<main>`, before the messages section:

```django
{% if user.is_authenticated and user.role == 'teacher' %}
  <nav class="breadcrumb-nav" aria-label="层级目录">
    <ol class="breadcrumb-list">
      {% block breadcrumb_items %}
        <li class="breadcrumb-item-current" aria-current="page">教师工作台</li>
      {% endblock %}
    </ol>
  </nav>
{% endif %}
```

- [ ] **Step 4: Add exact CSS behavior**

Change the desktop navigation rule and add breadcrumb/identity styles in `static/css/app.css`:

```css
.account-label { display: inline-flex; align-items: baseline; gap: var(--space-2); color: #dce8f4; font-size: var(--font-size-sm); }
.account-name { font-weight: var(--weight-semibold); }
.account-separator { color: rgba(255, 255, 255, .62); }
.account-username { color: #fff; }

.teacher-nav .teacher-nav-collapse { display: block; }

.breadcrumb-nav { margin-top: calc(var(--space-5) * -1); margin-bottom: var(--space-5); overflow-x: auto; scrollbar-width: thin; }
.breadcrumb-list { display: flex; align-items: center; width: max-content; min-width: 100%; margin: 0; padding: 0; list-style: none; color: var(--kt-muted); font-size: var(--font-size-sm); }
.breadcrumb-list li { display: inline-flex; align-items: center; min-height: 2rem; white-space: nowrap; }
.breadcrumb-list li + li::before { margin: 0 var(--space-2); color: var(--kt-border-strong); content: "/"; }
.breadcrumb-list a { color: var(--kt-brand-700); font-weight: var(--weight-medium); text-decoration: none; }
.breadcrumb-list a:hover { color: var(--kt-brand-900); text-decoration: underline; text-underline-offset: .2em; }
.breadcrumb-item-current { color: var(--kt-ink); font-weight: var(--weight-semibold); }
```

Inside the existing mobile media query, use this exact selector:

```css
.teacher-nav .teacher-nav-collapse:not(.show) { display: none; }
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m unittest tests.test_ui_contracts.UIContractTests.test_teacher_navigation_remains_visible_on_desktop tests.test_ui_contracts.UIContractTests.test_base_defines_accessible_teacher_breadcrumb_shell
node --check static/js/app.js
```

Expected: 2 Python tests pass; JavaScript syntax passes.

- [ ] **Step 6: Commit**

```powershell
git add tests/test_ui_contracts.py templates/base.html static/css/app.css
git commit -m "fix: expose teacher navigation hierarchy"
```

### Task 3: Declare hierarchy on every teacher page

**Files:**
- Modify: `tests/test_ui_contracts.py`
- Modify: all 12 non-dashboard templates under `templates/teacher/`

- [ ] **Step 1: Write the failing template-coverage test**

Add this method to `UIContractTests`:

```python
def test_every_teacher_subpage_declares_breadcrumb_items(self):
    template_root = ROOT / "templates" / "teacher"
    for path in template_root.glob("*.html"):
        if path.name == "dashboard.html":
            continue
        with self.subTest(template=path.name):
            source = path.read_text(encoding="utf-8")
            self.assertIn("{% block breadcrumb_items %}", source)
            self.assertIn('aria-current="page"', source)
            self.assertNotIn('class="back-link"', source)
```

- [ ] **Step 2: Run the test and verify red**

Run:

```powershell
python -m unittest tests.test_ui_contracts.UIContractTests.test_every_teacher_subpage_declares_breadcrumb_items
```

Expected: FAIL for every teacher subpage because none currently declares `breadcrumb_items`; six templates also retain redundant back links.

- [ ] **Step 3: Add explicit breadcrumb blocks**

Insert the corresponding block after `{% extends 'base.html' %}` in each template:

`templates/teacher/bank_list.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li class="breadcrumb-item-current" aria-current="page">题库</li>{% endblock %}
```

`templates/teacher/bank_detail.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li><a href="{% url 'question_bank:bank_list' %}">题库</a></li><li class="breadcrumb-item-current" aria-current="page">{{ bank.name }}</li>{% endblock %}
```

`templates/teacher/import_questions.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li><a href="{% url 'question_bank:bank_list' %}">题库</a></li><li><a href="{% url 'question_bank:bank_detail' bank.pk %}">{{ bank.name }}</a></li><li class="breadcrumb-item-current" aria-current="page">导入题目</li>{% endblock %}
```

`templates/teacher/import_kcs.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li><a href="{% url 'question_bank:bank_list' %}">题库</a></li><li><a href="{% url 'question_bank:bank_detail' bank.pk %}">{{ bank.name }}</a></li><li class="breadcrumb-item-current" aria-current="page">导入 KC</li>{% endblock %}
```

`templates/teacher/question_edit.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li><a href="{% url 'question_bank:bank_list' %}">题库</a></li><li><a href="{% url 'question_bank:bank_detail' bank.pk %}">{{ bank.name }}</a></li><li class="breadcrumb-item-current" aria-current="page">编辑 {{ question.code }}</li>{% endblock %}
```

`templates/teacher/concept_list.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li class="breadcrumb-item-current" aria-current="page">KC 库</li>{% endblock %}
```

`templates/teacher/class_list.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li class="breadcrumb-item-current" aria-current="page">班级</li>{% endblock %}
```

`templates/teacher/student_list.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li class="breadcrumb-item-current" aria-current="page">学生</li>{% endblock %}
```

`templates/teacher/experiment_list.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li class="breadcrumb-item-current" aria-current="page">实验</li>{% endblock %}
```

`templates/teacher/experiment_detail.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li><a href="{% url 'experiments:list' %}">实验</a></li><li class="breadcrumb-item-current" aria-current="page">{{ experiment.name }}</li>{% endblock %}
```

`templates/teacher/experiment_form.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li><a href="{% url 'experiments:list' %}">实验</a></li>{% if experiment %}<li><a href="{% url 'experiments:detail' experiment.pk %}">{{ experiment.name }}</a></li><li class="breadcrumb-item-current" aria-current="page">编辑配置</li>{% else %}<li class="breadcrumb-item-current" aria-current="page">新建实验</li>{% endif %}{% endblock %}
```

`templates/teacher/experiment_results.html`

```django
{% block breadcrumb_items %}<li><a href="{% url 'question_bank:dashboard' %}">教师工作台</a></li><li><a href="{% url 'experiments:list' %}">实验</a></li><li><a href="{% url 'experiments:detail' experiment.pk %}">{{ experiment.name }}</a></li><li class="breadcrumb-item-current" aria-current="page">采集结果</li>{% endblock %}
```

Remove each `<a class="back-link" ...>` from the six page headers because the breadcrumb now exposes the same parent as a stable, labeled link.

- [ ] **Step 4: Run the coverage test and template loader**

Run:

```powershell
python -m unittest tests.test_ui_contracts.UIContractTests.test_every_teacher_subpage_declares_breadcrumb_items
python manage.py test apps.accounts tests.test_ui_contracts
```

Expected: breadcrumb coverage passes; account and UI contract suites pass.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_ui_contracts.py templates/teacher
git commit -m "feat: add teacher workspace breadcrumbs"
```

### Task 4: Add participant management to the teacher dashboard workflow

**Files:**
- Modify: `tests/test_ui_contracts.py`
- Modify: `templates/teacher/dashboard.html`
- Modify: `static/css/app.css`

- [ ] **Step 1: Write the failing dashboard contract**

Add this method to `UIContractTests`:

```python
def test_teacher_dashboard_groups_all_six_workflow_destinations(self):
    source = (ROOT / "templates" / "teacher" / "dashboard.html").read_text(encoding="utf-8")
    for label in ("内容准备", "参与者管理", "实验采集", "管理题库", "管理 KC", "管理班级", "管理学生", "配置实验", "查看采集结果"):
        self.assertIn(label, source)
    self.assertIn("{% url 'accounts:class_list' %}", source)
    self.assertIn("{% url 'accounts:student_list' %}", source)
```

- [ ] **Step 2: Run the test and verify red**

Run:

```powershell
python -m unittest tests.test_ui_contracts.UIContractTests.test_teacher_dashboard_groups_all_six_workflow_destinations
```

Expected: FAIL because the dashboard has no participant-management links or three workflow groups.

- [ ] **Step 3: Replace the dashboard workflow links**

Replace `.workflow-links` in `templates/teacher/dashboard.html` with:

```django
<div class="workflow-stages">
  <article class="workflow-stage">
    <div class="workflow-stage-header"><span class="workflow-stage-index numeric">01</span><h3>内容准备</h3></div>
    <div class="workflow-stage-links">
      <a class="workflow-link" href="{% url 'question_bank:bank_list' %}"><span class="workflow-link-title">管理题库</span><span class="workflow-link-copy">创建、复制、导入并编辑题目。</span></a>
      <a class="workflow-link" href="{% url 'question_bank:concept_list' %}"><span class="workflow-link-title">管理 KC</span><span class="workflow-link-copy">检查知识点及题目关联。</span></a>
    </div>
  </article>
  <article class="workflow-stage">
    <div class="workflow-stage-header"><span class="workflow-stage-index numeric">02</span><h3>参与者管理</h3></div>
    <div class="workflow-stage-links">
      <a class="workflow-link" href="{% url 'accounts:class_list' %}"><span class="workflow-link-title">管理班级</span><span class="workflow-link-copy">维护学年、班级和可用状态。</span></a>
      <a class="workflow-link" href="{% url 'accounts:student_list' %}"><span class="workflow-link-title">管理学生</span><span class="workflow-link-copy">创建账号、分班并重置密码。</span></a>
    </div>
  </article>
  <article class="workflow-stage">
    <div class="workflow-stage-header"><span class="workflow-stage-index numeric">03</span><h3>实验采集</h3></div>
    <div class="workflow-stage-links">
      <a class="workflow-link" href="{% url 'experiments:list' %}"><span class="workflow-link-title">配置实验</span><span class="workflow-link-copy">设置题库、顺序、反馈、时间和学生分配。</span></a>
      <a class="workflow-link" href="{% url 'experiments:list' %}"><span class="workflow-link-title">查看采集结果</span><span class="workflow-link-copy">进入已发布实验的会话和导出页面。</span></a>
    </div>
  </article>
</div>
```

- [ ] **Step 4: Replace the old workflow CSS**

Use these styles in `static/css/app.css`:

```css
.workflow-stages { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid var(--kt-border); border-radius: var(--radius-surface); background: var(--kt-surface); overflow: hidden; }
.workflow-stage + .workflow-stage { border-left: 1px solid var(--kt-border); }
.workflow-stage-header { display: flex; align-items: baseline; gap: var(--space-3); padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--kt-border); background: var(--kt-surface-subtle); }
.workflow-stage-header h3 { margin: 0; font-size: var(--font-size-base); font-weight: var(--weight-bold); }
.workflow-stage-index { color: var(--kt-brand-700); font-size: var(--font-size-xs); font-weight: var(--weight-bold); letter-spacing: .06em; }
.workflow-stage-links { display: grid; }
.workflow-link { min-height: 7rem; padding: var(--space-5); color: var(--kt-ink); text-decoration: none; }
.workflow-link + .workflow-link { border-top: 1px solid var(--kt-border); }
.workflow-link:hover { background: var(--kt-brand-050); color: var(--kt-brand-900); }
.workflow-link-title { display: block; margin-bottom: var(--space-1); font-weight: var(--weight-bold); }
.workflow-link-copy { color: var(--kt-muted); font-size: var(--font-size-sm); }
```

Add these rules to the existing tablet breakpoint:

```css
.workflow-stages { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.workflow-stage:nth-child(3) { grid-column: 1 / -1; border-top: 1px solid var(--kt-border); border-left: 0; }
```

Add these rules to the existing mobile breakpoint:

```css
.workflow-stages { grid-template-columns: 1fr; }
.workflow-stage:nth-child(3) { grid-column: auto; }
.workflow-stage + .workflow-stage { border-top: 1px solid var(--kt-border); border-left: 0; }
```

- [ ] **Step 5: Run the dashboard contract and full UI-focused tests**

Run:

```powershell
python -m unittest tests.test_ui_contracts
python manage.py test apps.accounts
```

Expected: all UI contracts and account tests pass.

- [ ] **Step 6: Commit**

```powershell
git add tests/test_ui_contracts.py templates/teacher/dashboard.html static/css/app.css
git commit -m "feat: add participant workflow to teacher dashboard"
```

### Task 5: Documentation and final verification

**Files:**
- Modify: `docs/ui/UI-CHANGELOG.md`
- Modify: `SHA256SUMS.txt`

- [ ] **Step 1: Document the follow-up**

Add a 2026-08-04 follow-up section to `docs/ui/UI-CHANGELOG.md` recording:

```markdown
## 教师工作区层级修订

- 修复桌面端教师模块导航被 Bootstrap 折叠规则隐藏的问题。
- 为全部教师页面增加可点击的层级目录，并移除重复的返回链接。
- 教师端和学生端统一显示“姓名 · 登录账号”。
- 教师主页按内容准备、参与者管理和实验采集分组，补充班级与学生管理入口。
```

- [ ] **Step 2: Run the complete release gate**

Run:

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python scripts/verify_source.py
node --check static/js/app.js
node --check static/js/assessment.js
python manage.py collectstatic --noinput
git diff --check
```

Expected: system check clean; no model changes; all Django and source tests pass; JavaScript syntax passes; static collection succeeds; Git diff has no whitespace errors.

- [ ] **Step 3: Run authenticated route and template smoke checks**

Use Django setup plus `Client(HTTP_HOST="127.0.0.1")` to verify all 26 templates load and the teacher dashboard, bank, KC, class, student, experiment, student dashboard, and admin routes return HTTP 200. Inspect the teacher response for `aria-label="层级目录"`, `管理班级`, `管理学生`, and `演示教师 · teacher`.

Expected: every checked route returns 200 and every required marker is present.

- [ ] **Step 4: Perform visual verification if the in-app browser permits localhost navigation**

Check 1440×900 and 390×844 for:

- teacher module navigation visible and current module highlighted;
- breadcrumb hierarchy readable without wrapping into isolated characters;
- `姓名 · 账号` readable in teacher and student headers;
- three workflow stages at desktop and one column at mobile;
- no overlap with the recent-experiments panel.

If the browser security policy blocks localhost, record that exact limitation in `docs/ui/UI-CHANGELOG.md` and do not claim visual verification.

- [ ] **Step 5: Regenerate and validate `SHA256SUMS.txt`**

Exclude `.git`, virtual environments, caches, `staticfiles`, media, SQLite files, and the manifest itself. Write lowercase SHA-256 hashes using `./relative/path` and verify every entry against the current file.

- [ ] **Step 6: Commit documentation**

```powershell
git add docs/ui/UI-CHANGELOG.md SHA256SUMS.txt
git commit -m "docs: record teacher hierarchy verification"
```
