# 教师过程语言与班级设置收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将教师过程回放中的内部英文码转换为可解释的中文结论，并把班级元数据设置统一收敛到班级中心的“班级管理”页。

**Architecture:** 新增只负责教师显示语义的纯 Python 映射模块；drilldown 将内部 Coverage 与过程事件投影为映射后的展示字段，模板不再了解枚举或原因码。班级设置继续复用 ClassGroupUpdateForm、class_update 的事务、权限、CSRF、状态令牌和审计逻辑；班级中心管理页成为唯一常规入口，旧编辑 GET 只做兼容跳转，独立改名 POST 路由删除。

**Tech Stack:** Django 5、Python dataclasses、Django Templates、Bootstrap 5.3、原生 JavaScript、Django TestCase/TransactionTestCase。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| apps/analytics/teaching/process_language.py | Coverage 状态、时间质量、缺失原因和时间来源的教师中文映射；不访问数据库。 |
| apps/analytics/teaching/drilldown.py | 将映射后的展示字段填入 AttemptProcessDetail 和时间线步骤。 |
| templates/teacher/course_class_attempt_process.html | 仅渲染中文标题、说明和已转换的时间来源。 |
| apps/analytics/test_process_language.py | 映射模块的穷尽/回退单元测试。 |
| apps/analytics/test_process_projection.py | 过程详情不泄露内部枚举、原因码或 Run UUID 的集成测试。 |
| apps/accounts/course_resource_views.py | 为班级中心管理页提供 ClassGroupUpdateForm，复用保存逻辑并处理兼容跳转。 |
| templates/teacher/course_class_list.html | 移除班级列表的两个重复编辑控件。 |
| templates/teacher/course_class_center.html | 在 section=manage 内嵌统一班级设置表单。 |
| apps/accounts/course_urls.py、forms.py | 下线 class_rename 路由及其仅供该路由使用的表单。 |
| apps/accounts/tests_course_views.py、tests_collaboration.py、apps/analytics/test_class_center_views.py | 覆盖保存、错误、权限、兼容跳转、入口收敛和路由下线。 |
| docs/recording-capture-coverage-v1.md | 记录教师页与研究导出的显示边界。 |

### Task 1: 冻结教师过程语言映射

**Files:**

- Create: apps/analytics/teaching/process_language.py
- Create: apps/analytics/test_process_language.py

- [ ] **Step 1: 写入失败测试，覆盖已知码与未知码不泄露内部值**

~~~python
from django.test import SimpleTestCase

from apps.analytics.teaching.process_language import (
    coverage_reason_labels,
    coverage_status_message,
    event_time_source_message,
    time_quality_message,
)


class TeacherProcessLanguageTests(SimpleTestCase):
    def test_partial_coverage_uses_teacher_conclusion(self):
        message = coverage_status_message("partial")
        self.assertEqual(message.title, "过程记录不完整")
        self.assertIn("可确认最终结果", message.description)
        self.assertNotIn("partial", message.title + message.description)

    def test_exact_monotonic_explains_usable_interval_not_raw_code(self):
        message = time_quality_message("exact_monotonic")
        self.assertEqual(message.title, "步骤间隔可可靠比较")
        self.assertIn("同一页面", message.description)
        self.assertNotIn("exact_monotonic", message.title + message.description)

    def test_reason_and_event_time_source_are_chinese(self):
        self.assertEqual(
            coverage_reason_labels(("missing_item_left", "unknown_reason")),
            ("未记录离开题目", "存在未分类的记录缺口"),
        )
        self.assertEqual(
            event_time_source_message("server_received_at"),
            "服务器接收时间",
        )
~~~

- [ ] **Step 2: 运行测试，确认模块不存在而失败**

Run:

~~~powershell
.venv\Scripts\python.exe manage.py test apps.analytics.test_process_language -v 2
~~~

Expected: FAIL，错误指出 process_language 尚不存在。

- [ ] **Step 3: 实现纯展示映射模块**

~~~python
# apps/analytics/teaching/process_language.py
from dataclasses import dataclass


@dataclass(frozen=True)
class TeacherProcessMessage:
    title: str
    description: str


_COVERAGE_STATUS = {
    "complete": TeacherProcessMessage("过程记录完整", "已记录本题所需的过程边界，可用于过程回顾。"),
    "partial": TeacherProcessMessage("过程记录不完整", "可确认最终结果，但部分作答步骤未被记录，不能完整还原过程。"),
    "insufficient": TeacherProcessMessage("过程记录不足", "记录不足，不能分析作答过程。"),
    "invalid": TeacherProcessMessage("记录存在不一致", "答案快照、提交或评分事实彼此不一致。"),
    "legacy_unknown": TeacherProcessMessage("历史记录无法评估", "旧记录没有当前采集契约，不能按新标准判断。"),
    "not_applicable": TeacherProcessMessage("不适用", "当前题目或任务不要求该类过程记录。"),
    "pending_finalization": TeacherProcessMessage("过程记录等待确认", "本次作答刚结束，系统仍在等待可能迟到的过程记录。"),
}
_TIME_QUALITY = {
    "exact_monotonic": TeacherProcessMessage("步骤间隔可可靠比较", "同一页面内记录的先后顺序和步骤间隔可用于分析。"),
    "synchronized_estimate": TeacherProcessMessage("时间为同步估计", "可比较绝对时间，但属于校时估计。"),
    "client_wall_unsynchronized": TeacherProcessMessage("时间仅供参考", "有客户端时间记录，但未完成有效校时，不作精确间隔判断。"),
    "server_only": TeacherProcessMessage("仅有服务器记录时间", "可确认服务器收到或提交的顺序，不能视为学生实际操作时刻。"),
    "unknown": TeacherProcessMessage("时间质量无法判断", "不对步骤间隔作解释。"),
}
_REASONS = {
    "missing_final_snapshot": "未保存最终答案快照",
    "missing_item_left": "未记录离开题目",
    "missing_item_rendered": "未记录题目呈现",
    "missing_response_state": "未记录答案状态变化",
    "response_chain_break": "答案变化记录不连续",
    "sequence_gap": "过程记录序号存在缺口",
    "tail_gap": "页面结束前仍有未确认记录",
    "tail_unknown": "页面结束前仍有未确认记录",
    "checkpoint_missing": "未收到记录检查点",
    "time_sync_unavailable": "未完成有效时间同步",
    "cross_instance_order_uncertain": "跨页面先后顺序无法精确确定",
    "coverage_run_missing": "尚未生成记录质量评估",
    "pending_finalization": "过程记录仍在等待确认",
}


def coverage_status_message(status):
    return _COVERAGE_STATUS.get(
        status,
        TeacherProcessMessage("记录状态无法判断", "系统未能识别本次记录的质量状态。"),
    )


def time_quality_message(status):
    return _TIME_QUALITY.get(status, _TIME_QUALITY["unknown"])


def coverage_reason_labels(reasons):
    return tuple(_REASONS.get(reason, "存在未分类的记录缺口") for reason in reasons)


def event_time_source_message(source):
    return {
        "synchronized_estimate": "同步估计时间",
        "server_received_at": "服务器接收时间",
    }.get(source, "时间来源无法判断")
~~~

Keep these dictionaries module-private. Do not pass them to a template or export builder.

- [ ] **Step 4: 运行映射测试**

Run:

~~~powershell
.venv\Scripts\python.exe manage.py test apps.analytics.test_process_language -v 2
~~~

Expected: PASS，3 tests。

- [ ] **Step 5: 提交映射与测试**

~~~powershell
git add apps/analytics/teaching/process_language.py apps/analytics/test_process_language.py
git commit -m "feat: localize teacher process quality"
~~~

### Task 2: 用映射投影替换教师过程页的原始码

**Files:**

- Modify: apps/analytics/teaching/drilldown.py: AttemptProcessDetail、_process_map、build_attempt_process_detail
- Modify: templates/teacher/course_class_attempt_process.html
- Modify: apps/analytics/test_process_projection.py

- [ ] **Step 1: 写入失败集成测试**

在已有可构造 RecordingCoverageAttempt 的过程投影 fixture 后添加：

~~~python
detail = build_attempt_process_detail(
    self.class_group,
    experiment_id=self.experiment.pk,
    question_id=self.snapshot.pk,
    student_id=self.student.pk,
)
self.assertEqual(detail.coverage_status_title, "过程记录不完整")
self.assertEqual(detail.coverage_time_title, "步骤间隔可可靠比较")
self.assertEqual(detail.coverage_reason_labels, ("未记录离开题目",))
self.assertFalse(detail.has_cross_instance_order_uncertainty)

response = self.client.get(self.process_url)
for internal in (
    "partial",
    "exact_monotonic",
    "server_received_at",
    str(self.coverage.run_id),
):
    self.assertNotContains(response, internal)
self.assertContains(response, "过程记录不完整")
self.assertContains(response, "服务器接收时间")
~~~

另外构造同一 client_instance_id、同一 client_time_origin 的两条响应事件，断言对应 ProcessStep.time_source 是“服务器接收时间；与上一步的间隔可可靠比较”，且不含 exact_monotonic。

- [ ] **Step 2: 运行测试，确认新字段与展示尚不存在**

Run:

~~~powershell
.venv\Scripts\python.exe manage.py test apps.analytics.test_process_projection -v 2
~~~

Expected: FAIL，AttemptProcessDetail 不含 coverage_status_title 或页面仍含内部码。

- [ ] **Step 3: 改造详情数据对象与时间线投影**

将 AttemptProcessDetail 中仅给模板使用的字段替换为：

~~~python
coverage_status_title: str
coverage_status_description: str
coverage_time_title: str
coverage_time_description: str
coverage_reason_labels: tuple[str, ...]
has_cross_instance_order_uncertainty: bool
~~~

在 build_attempt_process_detail 内保留原始状态于局部变量，并只把映射结果塞进数据对象：

~~~python
status_message = coverage_status_message(coverage_status)
time_message = time_quality_message(
    coverage.time_quality if coverage else "unknown"
)
reason_labels = coverage_reason_labels(missing_reasons)

return AttemptProcessDetail(
    # 保留既有题目、学生、得分和 process 字段
    coverage_status_title=status_message.title,
    coverage_status_description=status_message.description,
    coverage_time_title=time_message.title,
    coverage_time_description=time_message.description,
    coverage_reason_labels=reason_labels,
    has_cross_instance_order_uncertainty=(
        "cross_instance_order_uncertain" in missing_reasons
    ),
)
~~~

在 _process_map 内，发生时间和排序保持不变；仅替换时间来源文案：

~~~python
source_key = (
    "synchronized_estimate"
    if source_event.estimated_occurred_at_utc
    else "server_received_at"
)
time_source = event_time_source_message(source_key)
if interval_ms is not None:
    time_source = f"{time_source}；与上一步的间隔可可靠比较"
~~~

不要改 Coverage 算法、occurred_at 的选择、排序或间隔计算。

- [ ] **Step 4: 更新模板，只渲染中文展示字段**

用以下卡片替换原始状态、CoverageRun UUID 和质量码：

~~~django
<article class="data-fact">
  <span>过程记录</span>
  <strong>{{ detail.coverage_status_title }}</strong>
  <small>{{ detail.coverage_status_description }}</small>
</article>
<article class="data-fact">
  <span>时间质量</span>
  <strong>{{ detail.coverage_time_title }}</strong>
  <small>{{ detail.coverage_time_description }}</small>
</article>
~~~

缺失原因面板使用：

~~~django
{{ detail.coverage_reason_labels|join:'；' }}
~~~

跨页面提示读取 detail.has_cross_instance_order_uncertainty。删除所有 detail.coverage_run_id、detail.coverage_status、detail.coverage_missing_reasons 与内部时间来源文字的模板引用。

- [ ] **Step 5: 运行目标测试与模板泄露检查**

Run:

~~~powershell
.venv\Scripts\python.exe manage.py test apps.analytics.test_process_projection apps.analytics.test_class_center_views -v 2
rg -n "partial|exact_monotonic|server_received_at|CoverageRun|coverage_missing_reasons" templates/teacher/course_class_attempt_process.html
~~~

Expected: Django tests PASS；rg 没有匹配结果。

- [ ] **Step 6: 提交过程页显示修改**

~~~powershell
git add apps/analytics/teaching/drilldown.py templates/teacher/course_class_attempt_process.html apps/analytics/test_process_projection.py
git commit -m "feat: clarify teacher process evidence"
~~~

### Task 3: 将班级设置表单收敛到班级管理页

**Files:**

- Modify: apps/accounts/course_resource_views.py: class_detail、class_edit、class_update
- Modify: apps/accounts/course_urls.py
- Modify: apps/accounts/forms.py
- Modify: templates/teacher/course_class_center.html
- Modify: templates/teacher/course_class_list.html
- Modify: apps/accounts/tests_course_views.py
- Modify: apps/accounts/tests_collaboration.py
- Modify: apps/analytics/test_class_center_views.py

- [ ] **Step 1: 写入失败测试，固定单一入口和保存行为**

在 test_class_center_views.py 的启用旗标测试加入：

~~~python
management = self.client.get(self.url, {"section": "manage"})
self.assertContains(management, "班级设置")
self.assertContains(
    management,
    'action="%s"' % reverse(
        "courses:class_update", args=[self.course.pk, self.class_group.pk]
    ),
)
self.assertContains(management, 'name="state_token"', html=False)

class_list = self.client.get(reverse("courses:classes", args=[self.course.pk]))
self.assertNotContains(class_list, "编辑班级")
self.assertNotContains(class_list, "编辑名称")
~~~

在 tests_course_views.py 将 token helper 改为从 ?section=manage 的 class_settings_form 读取。加入：

~~~python
with override_settings(CLASS_CENTER_ENABLED=True):
    response = self.client.get(
        reverse("courses:class_edit", args=[self.course.pk, class_group.pk])
    )
self.assertRedirects(
    response,
    f"{reverse('courses:class_detail', args=[self.course.pk, class_group.pk])}?section=manage",
)

with override_settings(CLASS_CENTER_ENABLED=True):
    response = self.client.post(update_url, valid_payload)
self.assertRedirects(
    response,
    f"{reverse('courses:class_detail', args=[self.course.pk, class_group.pk])}?section=manage",
)
~~~

对重复名称和过期 state_token 断言 status=200、模板为 course_class_center.html、active_section=manage、表单错误可见，数据库没有被覆盖。

- [ ] **Step 2: 运行测试，确认现状失败**

Run:

~~~powershell
.venv\Scripts\python.exe manage.py test apps.analytics.test_class_center_views apps.accounts.tests_course_views -v 2
~~~

Expected: FAIL，管理页没有 class_settings_form，列表仍包含两个编辑控件，更新仍返回旧详情页。

- [ ] **Step 3: 在班级中心管理段提供受保护的统一表单**

提取私有渲染 helper，避免 class_detail 和 class_update 重复构建查询、旗标和中心上下文：

~~~python
def _class_detail_response(
    request,
    course,
    class_group,
    *,
    class_settings_form=None,
    active_section_override=None,
    status=200,
):
    enrollments = class_group.enrollments.filter(is_active=True).select_related("student")
    query = request.GET.get("q", "").strip()
    if query:
        enrollments = enrollments.filter(
            Q(student__student_number__icontains=query)
            | Q(student__display_name__icontains=query)
            | Q(student__username__icontains=query)
        )
    context = {
        "course": course,
        "current_course": course,
        "class_group": class_group,
        "enrollments": enrollments,
        "course_classes": course.classes.filter(is_active=True),
        "query": query,
    }
    if not settings.CLASS_CENTER_ENABLED:
        return render(request, "teacher/course_class_detail.html", context, status=status)
    active_section = active_section_override or request.GET.get("section", "overview")
    if active_section not in {"overview", "students", "experiments", "manage"}:
        raise Http404
    experiment_id = _class_center_experiment_id(request)
    class_center = None
    if active_section != "manage":
        from apps.analytics.teaching.metrics import build_class_center_snapshot

        class_center = build_class_center_snapshot(
            class_group,
            experiment_id=experiment_id,
            query=query if active_section == "students" else "",
            sort=request.GET.get("sort", "student_number"),
            status=request.GET.get("status"),
            page=_class_center_page(request),
        )
        if experiment_id is not None and class_center.scope.task_count == 0:
            raise Http404
    context.update(
        {
            "active_section": active_section,
            "class_center": class_center,
            "selected_experiment_id": experiment_id,
            "student_sort": request.GET.get("sort", "student_number"),
            "student_status": request.GET.get("status", ""),
        }
    )
    if active_section == "manage" and course.status == course.Status.ACTIVE:
        context["class_settings_form"] = (
            class_settings_form or ClassGroupUpdateForm(instance=class_group)
        )
    return render(request, "teacher/course_class_center.html", context, status=status)
~~~

同时从现有 class_detail 抽出两个纯解析 helper，避免将非法查询参数静默转换：

~~~python
def _class_center_experiment_id(request):
    raw_experiment_id = request.GET.get("experiment_id", "").strip()
    if not raw_experiment_id:
        return None
    try:
        experiment_id = int(raw_experiment_id)
    except (TypeError, ValueError) as error:
        raise Http404 from error
    if experiment_id <= 0:
        raise Http404
    return experiment_id


def _class_center_page(request):
    try:
        return int(request.GET.get("page", "1"))
    except (TypeError, ValueError):
        return 1
~~~

class_detail 调用该 helper。class_update 保留其已有 select_for_update、state token、重复名称和审计行为；保存成功时：

~~~python
target = reverse("courses:class_detail", args=[course.pk, saved_class_group.pk])
if settings.CLASS_CENTER_ENABLED:
    target = f"{target}?section=manage"
return redirect(target)
~~~

保存失败时调用：

~~~python
return _class_detail_response(
    request,
    course,
    class_group,
    class_settings_form=form,
    active_section_override="manage",
)
~~~

在 course_class_center.html 的 manage 分支、成员管理 partial 前插入：

~~~django
{% if course.status == 'active' %}
<section class="card mb-4" aria-labelledby="class-settings-title">
  <div class="card-body p-md-4">
    <h2 class="form-section-title" id="class-settings-title">班级设置</h2>
    {% include 'components/error_summary.html' with form=class_settings_form %}
    <form method="post" action="{% url 'courses:class_update' course.pk class_group.pk %}" novalidate>
      {% csrf_token %}{{ class_settings_form.state_token }}
      <div class="row g-3">
        <div class="col-md-6">{% include 'components/form_field.html' with field=class_settings_form.name %}</div>
        <div class="col-md-6">{% include 'components/form_field.html' with field=class_settings_form.academic_year %}</div>
        <div class="col-12"><div class="form-check">{{ class_settings_form.is_active }}<label class="form-check-label" for="{{ class_settings_form.is_active.id_for_label }}">{{ class_settings_form.is_active.label }}</label></div><div class="form-help">停用后不能新增、导入、复制或转入学生；历史名单、转出、移出和密码重置仍可使用。</div></div>
      </div>
      <div class="form-actions"><button class="btn btn-primary" type="submit">保存班级设置</button></div>
    </form>
  </div>
</section>
{% endif %}
~~~

移除中心页 header 的“编辑班级”链接，但保留“导入学生”。归档课程继续由 require_writable_course 拒绝写入，管理页不显示设置表单。

- [ ] **Step 4: 删除列表重复控件，收敛旧路由**

在 course_class_list.html action cell 中保留“进入班级中心”和删除表单，删除 class_edit 链接与 details.inline-edit-control 整段。

class_edit 保留旧书签兼容：

~~~python
@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def class_edit(request, course_pk, class_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    class_group = get_object_or_404(course.classes, pk=class_pk)
    if settings.CLASS_CENTER_ENABLED:
        return redirect(
            f"{reverse('courses:class_detail', args=[course.pk, class_group.pk])}?section=manage"
        )
    return render(
        request,
        "teacher/course_class_edit.html",
        {
            "course": course,
            "current_course": course,
            "class_group": class_group,
            "form": ClassGroupUpdateForm(instance=class_group),
        },
    )
~~~

删除 class_rename 函数、ClassGroupRenameForm 的导入和类定义，以及 course_urls.py 中 rename/ 路由。不要删除 class_update；它是唯一写入入口。

- [ ] **Step 5: 更新协作、权限与路由测试**

将 tests_collaboration.py 的改名请求改为：

~~~python
form = ClassGroupUpdateForm(instance=self.class_group)
response = self.client.post(
    reverse("courses:class_update", args=[self.course.pk, self.class_group.pk]),
    {
        "name": "同步二班",
        "academic_year": self.class_group.academic_year,
        "is_active": "on",
        "state_token": form.initial["state_token"],
    },
)
~~~

删除只测试 class_rename 的测试。保留并改写重复名称、归档课程、停用班级并发、角色权限、CSRF、篡改令牌和 class.update 审计断言。新增：

~~~python
from django.urls import NoReverseMatch

with self.assertRaises(NoReverseMatch):
    reverse("courses:class_rename", args=[self.course.pk, self.class_group.pk])
~~~

- [ ] **Step 6: 运行班级目标回归**

Run:

~~~powershell
.venv\Scripts\python.exe manage.py test apps.accounts.tests_course_views apps.accounts.tests_collaboration apps.analytics.test_class_center_views -v 2
~~~

Expected: PASS；启用中心时只有一个设置表单入口，旧 edit/ GET 重定向，rename/ 没有命名路由。

- [ ] **Step 7: 提交班级管理收敛**

~~~powershell
git add apps/accounts/course_resource_views.py apps/accounts/course_urls.py apps/accounts/forms.py templates/teacher/course_class_list.html templates/teacher/course_class_center.html apps/accounts/tests_course_views.py apps/accounts/tests_collaboration.py apps/analytics/test_class_center_views.py
git commit -m "feat: centralize class settings"
~~~

### Task 4: 更新文档并执行界面/全量验证

**Files:**

- Modify: docs/recording-capture-coverage-v1.md
- Test: apps/analytics/test_process_projection.py、apps/analytics/test_class_center_views.py

- [ ] **Step 1: 更新产品数据契约文档中的教师显示边界**

在“RecordingCoverage 输出”后新增：

~~~markdown
教师过程页将完整性与时间质量转换为中文教学结论，例如“过程记录不完整”“步骤间隔可可靠比较”。原始 Coverage 状态码、缺失原因码、CoverageRun UUID 和事件时间来源枚举只在数据中心研究导出中保留。教师页的“服务器接收时间”不表示学生实际操作时刻；缺失记录不解释为学生未操作。
~~~

- [ ] **Step 2: 运行完整自动化门禁**

Run:

~~~powershell
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe manage.py test
.venv\Scripts\python.exe manage.py collectstatic --noinput
node --check static/js/app.js
node --check static/js/assessment.js
node --check static/js/process-event-collector.js
node --test apps/assessment/tests_js/process-event-collector.test.mjs
git diff --check
~~~

Expected: check 无问题，迁移无漂移，Django/Node 测试全绿，静态文件和差异检查无错误。

- [ ] **Step 3: 浏览器烟测教师入口和过程页**

以 CLASS_CENTER_ENABLED=1 启动本地服务，验证：

1. 班级列表不显示“编辑班级”“编辑名称”，仍显示“进入班级中心”。
2. 班级中心 ?section=manage 显示班级设置、成员管理和导入学生；保存后留在 ?section=manage。
3. 教师过程页只出现中文状态、中文缺失原因、中文时间来源和说明；页面正文不出现 partial、exact_monotonic、server_received_at 或 CoverageRun UUID。
4. 1440×900、390×844 和等效 200% 缩放无页面级横向溢出；表格保持组件内滚动。

- [ ] **Step 4: 提交文档与验证结果**

~~~powershell
git add docs/recording-capture-coverage-v1.md
git commit -m "docs: clarify teacher process evidence"
git status --short
~~~

Expected: 除用户已有的 .audit_data_center_v3/ 外没有未提交产品文件。

## 自检结果

- 规格中的教师汉化、用途说明、原因映射、隐藏研究内部标识、列表入口收敛、管理页表单、旧编辑地址兼容、独立改名路由下线、权限/并发/CSRF/审计及响应式验证，分别覆盖于 Task 1–4。
- 文档中没有 TODO、TBD、未定义函数或依赖后续任务才定义的展示映射。
- 未改变 Coverage 算法、原始事件、导出字段或学生端数据可见性。
