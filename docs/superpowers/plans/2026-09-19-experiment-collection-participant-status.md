# Single Experiment Collection Participant Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add complete single-experiment collection status totals and a server-filtered student status detail table without changing assessment, deadline, grading, or analytics behavior.

**Architecture:** Keep the existing class-task progress endpoint authoritative for class totals and expose its existing `expired` and `invalidated` fields in the UI. Add one read-only experiment-level participant collection endpoint backed by immutable participant snapshots, annotate the optional task session through a bounded subquery, derive `not_started` only when no session exists, and paginate/filter on the server. Load the student detail only when the collection tab is active and persist tab/filter/page state in the route query.

**Tech Stack:** Django 5.2, Django REST Framework, drf-spectacular/OpenAPI, PostgreSQL 17, Vue 3.5, TypeScript, Vue Router, Vitest.

---

## File map

- Create `apps/experiments/collection_status.py`: status constants, snapshot-based annotated queryset, and row projection.
- Modify `apps/experiments/api/serializers.py`: query, row, choice, filter, and collection response serializers.
- Modify `apps/experiments/api/views.py`: authenticated read-only participant collection view and 25-row paginator.
- Modify `apps/experiments/api/urls.py`: route for the new experiment-level collection endpoint.
- Modify `apps/experiments/test_api.py`: API state, filter, permission, scope, pagination, and query-growth tests.
- Modify `openapi-schema.yml`: generated endpoint and schemas.
- Modify `frontend/src/api/generated/schema.d.ts`: generated TypeScript schema.
- Modify `frontend/src/features/teacher-experiments/api.ts`: typed participant collection request.
- Modify `frontend/src/features/teacher-experiments/TeacherExperimentDetail.vue`: route-backed collection tab, complete totals, filters, paginated desktop/mobile detail.
- Modify `frontend/src/features/teacher-experiments/TeacherExperimentDetail.test.ts`: lazy-loading, complete totals, filter routing, pagination, empty/error, and mobile facts.

Execution starts in an isolated worktree/branch named `feat/experiment-collection-participant-status` created from the current approved documentation commit.

### Task 1: Add failing API coverage for the five participant states

**Files:**
- Modify: `apps/experiments/test_api.py`

- [ ] **Step 1: Add imports and a reusable published collection fixture**

Add `Counter`, `CaptureQueriesContext`, `connection`, and `ExperimentSession` imports, then add this helper to `TeacherPublishPlanApiTests`:

```python
from collections import Counter

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.assessment.models import ExperimentSession


def publish_collection_status_fixture(self, *, extra_student_count=0):
    state_students = []
    for suffix, display_name in (
        ("101", "进行中学生"),
        ("102", "正常完成学生"),
        ("103", "超时学生"),
        ("104", "作废学生"),
    ):
        student = User.objects.create_user(
            username=f"PUB-{suffix}",
            student_number=f"PUB-{suffix}",
            display_name=display_name,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_a,
            student=student,
        )
        state_students.append(student)

    for index in range(extra_student_count):
        student = User.objects.create_user(
            username=f"PUB-EXTRA-{index:03d}",
            student_number=f"PUB-EXTRA-{index:03d}",
            display_name=f"分页学生{index:03d}",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_a,
            student=student,
        )

    plan, _opens_at = self.preview_two_classes()
    response = self.client.post(
        self.plan_url(plan, "commit/"),
        data=json.dumps({"expected_revision": plan.plan_revision}),
        content_type="application/json",
    )
    self.complete_operation(response)
    task = ClassExperimentTask.objects.get(
        experiment=self.experiment,
        class_group=self.class_a,
    )
    snapshots = {
        row.student.student_number: row
        for row in task.participant_snapshots.select_related("student")
    }
    for student, status in zip(
        state_students,
        (
            ExperimentSession.Status.IN_PROGRESS,
            ExperimentSession.Status.COMPLETED,
            ExperimentSession.Status.EXPIRED,
            ExperimentSession.Status.INVALIDATED,
        ),
        strict=True,
    ):
        snapshot = snapshots[student.student_number]
        ExperimentSession.objects.create(
            version=task.release_snapshot,
            class_task=task,
            participant_snapshot=snapshot,
            class_snapshot=task.class_snapshot,
            student=student,
            status=status,
            submission_count=2,
        )
    return task
```

- [ ] **Step 2: Write failing endpoint and filter tests**

Add these tests to `TeacherPublishPlanApiTests`:

```python
def collection_url(self, suffix=""):
    base = (
        f"/api/v1/teacher/experiments/{self.experiment.pk}/"
        "collection-participants/"
    )
    return f"{base}{suffix}"


def test_collection_participants_expose_all_five_states_and_filters(self):
    task = self.publish_collection_status_fixture()

    response = self.client.get(
        self.collection_url(f"?class_id={task.class_group_id}")
    )

    self.assertEqual(response.status_code, 200, response.content)
    payload = response.json()
    self.assertEqual(payload["page"]["size"], 25)
    self.assertEqual(payload["page"]["total_items"], 5)
    self.assertEqual(
        Counter(item["status"] for item in payload["items"]),
        Counter({
            "not_started": 1,
            "in_progress": 1,
            "completed": 1,
            "expired": 1,
            "invalidated": 1,
        }),
    )
    not_started = next(
        item for item in payload["items"] if item["status"] == "not_started"
    )
    self.assertIsNone(not_started["started_at"])
    self.assertIsNone(not_started["ended_at"])
    self.assertEqual(not_started["submission_count"], 0)
    self.assertEqual(
        [choice["value"] for choice in payload["status_choices"]],
        ["not_started", "in_progress", "completed", "expired", "invalidated"],
    )

    expired = self.client.get(self.collection_url(
        f"?class_id={task.class_group_id}&status=expired&q=PUB-103"
    ))
    self.assertEqual(expired.status_code, 200, expired.content)
    self.assertEqual(expired.json()["page"]["total_items"], 1)
    self.assertEqual(expired.json()["items"][0]["student_number"], "PUB-103")
    self.assertEqual(expired.json()["filters"]["status"], "expired")

    by_name = self.client.get(self.collection_url("?q=超时学生"))
    self.assertEqual(by_name.status_code, 200, by_name.content)
    self.assertEqual(by_name.json()["items"][0]["student_number"], "PUB-103")


def test_collection_participants_reject_invalid_filters_and_hidden_scope(self):
    self.publish_collection_status_fixture()
    unrelated_class = ClassGroup.objects.create(
        course=self.course,
        name="未发布班",
        academic_year="2026",
    )

    invalid_status = self.client.get(self.collection_url("?status=unknown"))
    invalid_class = self.client.get(
        self.collection_url(f"?class_id={unrelated_class.pk}")
    )
    invalid_page = self.client.get(self.collection_url("?page=0"))

    self.assertEqual(invalid_status.status_code, 400)
    self.assertEqual(invalid_class.status_code, 400)
    self.assertEqual(invalid_page.status_code, 400)

    other_teacher = User.objects.create_user(
        username="collection-other-teacher",
        role=User.Role.TEACHER,
    )
    self.client.force_login(other_teacher)
    hidden = self.client.get(self.collection_url())
    self.assertEqual(hidden.status_code, 404)


def test_collection_participant_query_count_does_not_grow_per_row(self):
    task = self.publish_collection_status_fixture(extra_student_count=30)
    url = self.collection_url(f"?class_id={task.class_group_id}")

    with CaptureQueriesContext(connection) as captured:
        response = self.client.get(url)

    self.assertEqual(response.status_code, 200, response.content)
    self.assertLessEqual(len(captured), 10)


def test_collection_participant_pagination_is_stable_and_disjoint(self):
    task = self.publish_collection_status_fixture(extra_student_count=30)
    base = f"?class_id={task.class_group_id}"

    first = self.client.get(self.collection_url(base))
    second = self.client.get(self.collection_url(f"{base}&page=2"))
    repeated_first = self.client.get(self.collection_url(base))

    self.assertEqual(first.status_code, 200, first.content)
    self.assertEqual(second.status_code, 200, second.content)
    first_ids = [item["participant_id"] for item in first.json()["items"]]
    second_ids = [item["participant_id"] for item in second.json()["items"]]
    self.assertEqual(len(first_ids), 25)
    self.assertTrue(second_ids)
    self.assertFalse(set(first_ids) & set(second_ids))
    self.assertEqual(
        first_ids,
        [item["participant_id"] for item in repeated_first.json()["items"]],
    )
```

- [ ] **Step 3: Run the tests and verify the endpoint is missing**

Run:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.experiments.test_api.TeacherPublishPlanApiTests.test_collection_participants_expose_all_five_states_and_filters apps.experiments.test_api.TeacherPublishPlanApiTests.test_collection_participants_reject_invalid_filters_and_hidden_scope apps.experiments.test_api.TeacherPublishPlanApiTests.test_collection_participant_query_count_does_not_grow_per_row apps.experiments.test_api.TeacherPublishPlanApiTests.test_collection_participant_pagination_is_stable_and_disjoint
```

Expected: FAIL with `404` responses for `collection-participants/`; no implementation should be added before this failure is observed.

- [ ] **Step 4: Commit the failing tests**

```powershell
git add apps/experiments/test_api.py
git commit -m "test: define experiment collection participant states"
```

### Task 2: Implement the snapshot-backed collection participant endpoint

**Files:**
- Create: `apps/experiments/collection_status.py`
- Modify: `apps/experiments/api/serializers.py`
- Modify: `apps/experiments/api/views.py`
- Modify: `apps/experiments/api/urls.py`
- Test: `apps/experiments/test_api.py`

- [ ] **Step 1: Create the bounded queryset and row projection**

Create `apps/experiments/collection_status.py` with this complete implementation:

```python
from django.db.models import (
    CharField,
    DateTimeField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce

from apps.assessment.models import ExperimentSession

from .models import ExperimentVersionParticipantSnapshot


COLLECTION_STATUS_CHOICES = (
    ("not_started", "未开始"),
    (ExperimentSession.Status.IN_PROGRESS, "进行中"),
    (ExperimentSession.Status.COMPLETED, "正常完成"),
    (ExperimentSession.Status.EXPIRED, "已超时"),
    (ExperimentSession.Status.INVALIDATED, "已作废"),
)
COLLECTION_STATUS_LABELS = dict(COLLECTION_STATUS_CHOICES)


def collection_participant_queryset(experiment):
    session = ExperimentSession.objects.filter(
        class_task_id=OuterRef("class_task_id"),
        participant_snapshot_id=OuterRef("pk"),
    )
    return (
        ExperimentVersionParticipantSnapshot.objects.filter(
            class_task__experiment=experiment,
        )
        .select_related("student", "class_task__class_group")
        .annotate(
            collection_status=Coalesce(
                Subquery(session.values("status")[:1]),
                Value("not_started"),
                output_field=CharField(),
            ),
            session_started_at=Subquery(
                session.values("started_at")[:1], output_field=DateTimeField()
            ),
            session_ended_at=Subquery(
                session.values("terminal_at")[:1], output_field=DateTimeField()
            ),
            session_submission_count=Coalesce(
                Subquery(
                    session.values("submission_count")[:1],
                    output_field=IntegerField(),
                ),
                Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by(
            "class_task__class_group__academic_year",
            "class_task__class_group__name",
            "student__student_number",
            "pk",
        )
    )


def filter_collection_participants(queryset, *, class_id=None, status="", query=""):
    if class_id is not None:
        queryset = queryset.filter(class_task__class_group_id=class_id)
    if status:
        queryset = queryset.filter(collection_status=status)
    query = query.strip()
    if query:
        queryset = queryset.filter(
            Q(student__student_number__icontains=query)
            | Q(student__display_name__icontains=query)
            | Q(student__first_name__icontains=query)
            | Q(student__last_name__icontains=query)
            | Q(student__username__icontains=query)
        )
    return queryset


def collection_participant_payload(participant):
    class_group = participant.class_task.class_group
    return {
        "participant_id": participant.pk,
        "student_id": participant.student_id,
        "student_number": participant.student.student_number
        or participant.student.username,
        "display_name": participant.student.shown_name,
        "class_id": class_group.pk,
        "class_name": str(class_group),
        "status": participant.collection_status,
        "status_label": COLLECTION_STATUS_LABELS[participant.collection_status],
        "started_at": participant.session_started_at,
        "ended_at": participant.session_ended_at,
        "submission_count": participant.session_submission_count,
    }
```

- [ ] **Step 2: Add strict request and response serializers**

In `apps/experiments/api/serializers.py`, import `COLLECTION_STATUS_CHOICES` and add:

```python
from ..collection_status import COLLECTION_STATUS_CHOICES


class CollectionParticipantQuerySerializer(serializers.Serializer):
    class_id = serializers.IntegerField(min_value=1, required=False)
    status = serializers.ChoiceField(
        choices=COLLECTION_STATUS_CHOICES,
        required=False,
        allow_blank=True,
    )
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)
    page = serializers.IntegerField(min_value=1, required=False)


class CollectionChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class CollectionClassChoiceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class CollectionParticipantRowSerializer(serializers.Serializer):
    participant_id = serializers.IntegerField()
    student_id = serializers.IntegerField()
    student_number = serializers.CharField()
    display_name = serializers.CharField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    status = serializers.ChoiceField(choices=COLLECTION_STATUS_CHOICES)
    status_label = serializers.CharField()
    started_at = serializers.DateTimeField(allow_null=True)
    ended_at = serializers.DateTimeField(allow_null=True)
    submission_count = serializers.IntegerField(min_value=0)


class CollectionParticipantFilterSerializer(serializers.Serializer):
    class_id = serializers.IntegerField(allow_null=True)
    status = serializers.CharField(allow_blank=True)
    q = serializers.CharField(allow_blank=True)


class CollectionParticipantCollectionSerializer(serializers.Serializer):
    items = CollectionParticipantRowSerializer(many=True)
    page = PageMetadataSerializer()
    filters = CollectionParticipantFilterSerializer()
    class_choices = CollectionClassChoiceSerializer(many=True)
    status_choices = CollectionChoiceSerializer(many=True)
```

- [ ] **Step 3: Add the view and route**

In `apps/experiments/api/views.py`, import the new service functions/model/serializers, then add:

```python
from rest_framework.exceptions import NotFound

from ..collection_status import (
    COLLECTION_STATUS_CHOICES,
    collection_participant_payload,
    collection_participant_queryset,
    filter_collection_participants,
)
from .serializers import (
    CollectionParticipantCollectionSerializer,
    CollectionParticipantQuerySerializer,
)


class CollectionParticipantPagination(FixedPageNumberPagination):
    page_size = 25


class TeacherExperimentCollectionParticipantView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    @extend_schema(
        operation_id="teacher_experiment_collection_participant_list",
        parameters=[CollectionParticipantQuerySerializer],
        responses=CollectionParticipantCollectionSerializer,
    )
    def get(self, request, experiment_id):
        experiment = get_object_or_404(
            Experiment.objects.select_related("course"), pk=experiment_id
        )
        if not experiment.course.can_manage(request.user):
            raise Http404

        query_serializer = CollectionParticipantQuerySerializer(
            data=request.query_params
        )
        query_serializer.is_valid(raise_exception=True)
        filters = query_serializer.validated_data
        class_id = filters.get("class_id")
        class_rows = list(
            experiment.class_tasks.select_related("class_group").order_by(
                "class_group__academic_year", "class_group__name", "pk"
            )
        )
        valid_class_ids = {row.class_group_id for row in class_rows}
        if class_id is not None and class_id not in valid_class_ids:
            raise ValidationError({"class_id": "该班级不属于当前实验。"})

        queryset = filter_collection_participants(
            collection_participant_queryset(experiment),
            class_id=class_id,
            status=filters.get("status", ""),
            query=filters.get("q", ""),
        )
        paginator = CollectionParticipantPagination()
        try:
            page = paginator.paginate_queryset(queryset, request, view=self)
        except NotFound as exc:
            raise ValidationError({"page": "页码超出范围。"}) from exc
        response = paginator.get_paginated_response(
            [collection_participant_payload(row) for row in page]
        )
        response.data.update(
            {
                "filters": {
                    "class_id": class_id,
                    "status": filters.get("status", ""),
                    "q": filters.get("q", "").strip(),
                },
                "class_choices": [
                    {"id": row.class_group_id, "name": str(row.class_group)}
                    for row in class_rows
                ],
                "status_choices": [
                    {"value": value, "label": label}
                    for value, label in COLLECTION_STATUS_CHOICES
                ],
            }
        )
        return response
```

In `apps/experiments/api/urls.py`, import the view and register:

```python
path(
    "experiments/<int:experiment_id>/collection-participants/",
    TeacherExperimentCollectionParticipantView.as_view(),
    name="teacher-experiment-collection-participant-list",
),
```

- [ ] **Step 4: Run the new tests and fix only concrete failures**

Run the same four tests from Task 1. Expected: all four PASS.

- [ ] **Step 5: Run the existing class-task API regression**

Run:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.experiments.test_api.TeacherPublishPlanApiTests.test_class_task_projection_progress_and_terminal_actions_use_server_state
```

Expected: PASS; existing task totals and actions remain unchanged.

- [ ] **Step 6: Commit the backend endpoint**

```powershell
git add apps/experiments/collection_status.py apps/experiments/api/serializers.py apps/experiments/api/views.py apps/experiments/api/urls.py apps/experiments/test_api.py
git commit -m "feat: expose experiment collection participants"
```

### Task 3: Regenerate the API contract and add the typed client

**Files:**
- Modify: `openapi-schema.yml`
- Modify: `frontend/src/api/generated/schema.d.ts`
- Modify: `frontend/src/features/teacher-experiments/api.ts`

- [ ] **Step 1: Generate and validate OpenAPI artifacts**

Prepare the required Node/PostgreSQL paths, then run:

```powershell
$env:PATH='C:\Users\31606\Downloads\ATOP-tooling\node-v24.20.0-win-x64;C:\Users\31606\Downloads\ATOP-tooling\PostgreSQL-17.11-portable\pgsql\bin;' + $env:PATH
$env:POSTGRES_HOST='127.0.0.1'
$env:POSTGRES_PORT='55432'
$env:POSTGRES_USER='kt_user'
$env:POSTGRES_PASSWORD='kt_password'
$env:POSTGRES_DB='kt_platform'
npm --prefix frontend run api:generate
npm --prefix frontend run api:check
```

Expected: generation exits 0; `api:check` reports generated types are current.

- [ ] **Step 2: Add the typed API request**

In `frontend/src/features/teacher-experiments/api.ts`, add:

```typescript
export type CollectionParticipantCollection = components['schemas']['CollectionParticipantCollection']
export type CollectionParticipantFilters = {
  classId?: number
  status?: string
  query?: string
  page?: number
}
```

Add this method to `createTeacherExperimentApi()`:

```typescript
collectionParticipants(experimentId: number, filters: CollectionParticipantFilters = {}) {
  const query = new URLSearchParams()
  if (filters.classId) query.set('class_id', String(filters.classId))
  if (filters.status) query.set('status', filters.status)
  if (filters.query?.trim()) query.set('q', filters.query.trim())
  if (filters.page && filters.page > 1) query.set('page', String(filters.page))
  const suffix = query.size ? `?${query.toString()}` : ''
  return client.get<CollectionParticipantCollection>(
    `/api/v1/teacher/experiments/${experimentId}/collection-participants/${suffix}`,
  )
},
```

- [ ] **Step 3: Run TypeScript checking**

```powershell
npm --prefix frontend run typecheck
```

Expected: PASS with no type errors.

- [ ] **Step 4: Commit the contract and client**

```powershell
git add openapi-schema.yml frontend/src/api/generated/schema.d.ts frontend/src/features/teacher-experiments/api.ts
git commit -m "feat: add typed collection participant api"
```

### Task 4: Add failing frontend tests for totals, lazy detail, routing, and pagination

**Files:**
- Modify: `frontend/src/features/teacher-experiments/TeacherExperimentDetail.test.ts`

- [ ] **Step 1: Extend the router and API mocks**

Replace the fixed route mock with mutable, hoist-safe route state and add the participant request:

```typescript
const mocks = vi.hoisted(() => ({
  route: {
    params: { courseId: '7', experimentId: '11' },
    query: {} as Record<string, string>,
  },
  api: {
    detail: vi.fn(), readiness: vi.fn(), classTasks: vi.fn(), progress: vi.fn(),
    collectionParticipants: vi.fn(), startPlan: vi.fn(), reviseSchedule: vi.fn(),
    cancel: vi.fn(), manualClose: vi.fn(), releaseFeedback: vi.fn(),
  },
  deletion: {
    previewExperiment: vi.fn(), deleteExperiment: vi.fn(),
    previewClassExperiment: vi.fn(), removeClassExperiment: vi.fn(),
    previewClass: vi.fn(), deleteClass: vi.fn(), cleanup: vi.fn(),
  },
  push: vi.fn(),
  replace: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}))
```

Reset `mocks.route.query = {}` in `beforeEach()` and return a default participant collection:

```typescript
mocks.api.collectionParticipants.mockResolvedValue({
  items: [],
  page: { number: 1, size: 25, total_items: 0, total_pages: 1, start: 0, end: 0 },
  filters: { class_id: null, status: '', q: '' },
  class_choices: [{ id: 5, name: '2024 实验班' }],
  status_choices: [
    { value: 'not_started', label: '未开始' },
    { value: 'in_progress', label: '进行中' },
    { value: 'completed', label: '正常完成' },
    { value: 'expired', label: '已超时' },
    { value: 'invalidated', label: '已作废' },
  ],
})
```

- [ ] **Step 2: Add failing collection tab tests**

```typescript
it('shows complete task totals and lazily loads participant states', async () => {
  mocks.api.detail.mockResolvedValue({
    id: 11, name: '认知诊断实验', description: '', question_bank: null,
    status: 'published', status_label: '已发布', can_manage: true,
    updated_at: '2026-09-19T00:00:00Z',
  })
  mocks.api.classTasks.mockResolvedValue({ items: [{
    id: 31, class_id: 5, class_name: '2024 实验班', participant_count: 5,
    opens_at: null, closes_at: null, business_status: 'open', grading: {},
    available_actions: [],
  }] })
  mocks.api.progress.mockResolvedValue({
    task_id: 31, total_participants: 5, not_started: 1, in_progress: 1,
    completed: 1, expired: 1, invalidated: 1,
  })
  mocks.api.collectionParticipants.mockResolvedValue({
    items: [{
      participant_id: 9, student_id: 19, student_number: 'S019',
      display_name: '超时学生', class_id: 5, class_name: '2024 实验班',
      status: 'expired', status_label: '已超时',
      started_at: '2026-09-19T01:00:00Z', ended_at: '2026-09-19T02:00:00Z',
      submission_count: 2,
    }],
    page: { number: 1, size: 25, total_items: 1, total_pages: 1, start: 1, end: 1 },
    filters: { class_id: null, status: '', q: '' },
    class_choices: [{ id: 5, name: '2024 实验班' }],
    status_choices: [{ value: 'expired', label: '已超时' }],
  })
  const wrapper = mount(TeacherExperimentDetail)
  await flushPromises()
  expect(mocks.api.collectionParticipants).not.toHaveBeenCalled()

  await wrapper.get('#experiment-tab-collection').trigger('click')
  await flushPromises()

  expect(mocks.api.collectionParticipants).toHaveBeenCalledWith(11, {
    classId: undefined, status: undefined, query: undefined, page: 1,
  })
  expect(wrapper.get('#collection-status-title').text()).toContain('班级任务进度')
  expect(wrapper.get('[data-collection-summary]').text()).toContain('正常完成')
  expect(wrapper.get('[data-collection-summary]').text()).toContain('已超时')
  expect(wrapper.get('[data-collection-summary]').text()).toContain('已作废')
  expect(wrapper.get('[data-collection-participants]').text()).toContain('超时学生')
  expect(wrapper.get('[data-collection-participants]').text()).toContain('已超时')
})

it('persists collection filters and page in the route query', async () => {
  const wrapper = mount(TeacherExperimentDetail)
  await flushPromises()
  await wrapper.get('#experiment-tab-collection').trigger('click')
  await flushPromises()

  await wrapper.get('[data-collection-class]').setValue('5')
  await wrapper.get('[data-collection-status]').setValue('expired')
  await wrapper.get('[data-collection-query]').setValue('S019')
  await wrapper.get('[data-collection-filter]').trigger('submit')
  await flushPromises()

  expect(mocks.replace).toHaveBeenLastCalledWith({ query: {
    tab: 'collection', class_id: '5', status: 'expired', q: 'S019',
  } })
  expect(mocks.api.collectionParticipants).toHaveBeenLastCalledWith(11, {
    classId: 5, status: 'expired', query: 'S019', page: 1,
  })
})
```

- [ ] **Step 3: Run the component tests and verify they fail**

```powershell
npm --prefix frontend run test:unit -- TeacherExperimentDetail.test.ts
```

Expected: FAIL because the collection participant method/UI and complete status columns are not implemented.

- [ ] **Step 4: Commit the failing frontend tests**

```powershell
git add frontend/src/features/teacher-experiments/TeacherExperimentDetail.test.ts
git commit -m "test: define collection status interface"
```

### Task 5: Implement the collection tab filters and complete status display

**Files:**
- Modify: `frontend/src/features/teacher-experiments/TeacherExperimentDetail.vue`
- Test: `frontend/src/features/teacher-experiments/TeacherExperimentDetail.test.ts`

- [ ] **Step 1: Add route-backed state and lazy loading**

Add `watch` to the Vue import, import `MobileCardList` and `Paginator`, add typed participant state, and add these functions:

```typescript
import { computed, inject, onMounted, reactive, ref, watch, type Ref } from 'vue'
import MobileCardList from '@/design-system/components/MobileCardList.vue'
import Paginator from '@/design-system/components/Paginator.vue'
import type { CollectionParticipantCollection } from './api'

const collection = ref<CollectionParticipantCollection>()
const collectionLoading = ref(false)
const collectionError = ref('')
const collectionClassId = ref(String(route.query.class_id || ''))
const collectionStatus = ref(String(route.query.status || ''))
const collectionQuery = ref(String(route.query.q || ''))
const handledCollectionRouteKey = ref('')

function collectionDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('zh-CN') : '—'
}

function collectionFilters(page = Number(route.query.page || 1)) {
  return {
    classId: collectionClassId.value ? Number(collectionClassId.value) : undefined,
    status: collectionStatus.value || undefined,
    query: collectionQuery.value.trim() || undefined,
    page,
  }
}

function normalizedCollectionPage(value: unknown) {
  const page = Number(value || 1)
  return Number.isInteger(page) && page > 0 ? page : 1
}

function collectionRouteKey(query: Record<string, unknown>) {
  return JSON.stringify([
    String(query.tab || ''),
    String(query.class_id || ''),
    String(query.status || ''),
    String(query.q || ''),
    normalizedCollectionPage(query.page),
  ])
}

function collectionRouteQuery(page = 1): Record<string, string> {
  const query: Record<string, string> = { tab: 'collection' }
  if (collectionClassId.value) query.class_id = collectionClassId.value
  if (collectionStatus.value) query.status = collectionStatus.value
  if (collectionQuery.value.trim()) query.q = collectionQuery.value.trim()
  if (page > 1) query.page = String(page)
  return query
}

async function replaceCollectionRoute(query: Record<string, string>) {
  handledCollectionRouteKey.value = collectionRouteKey(query)
  await router.replace({ query })
}

async function loadCollection(page = Number(route.query.page || 1)) {
  collectionLoading.value = true
  collectionError.value = ''
  collection.value = undefined
  try {
    collection.value = await api.collectionParticipants(
      experimentId,
      collectionFilters(page),
    )
  } catch (caught) {
    collectionError.value = caught instanceof Error
      ? caught.message
      : '学生采集状态加载失败。'
  } finally {
    collectionLoading.value = false
  }
}

async function selectTab(tab: 'definition' | 'tasks' | 'collection') {
  activeTab.value = tab
  if (tab !== 'collection') {
    handledCollectionRouteKey.value = collectionRouteKey({})
    await router.replace({ query: {} })
    return
  }
  await replaceCollectionRoute(collectionRouteQuery(normalizedCollectionPage(route.query.page)))
  if (tab === 'collection' && !collection.value) await loadCollection()
}

async function applyCollectionFilters() {
  await replaceCollectionRoute(collectionRouteQuery(1))
  await loadCollection(1)
}

async function clearCollectionFilters() {
  collectionClassId.value = ''
  collectionStatus.value = ''
  collectionQuery.value = ''
  await replaceCollectionRoute(collectionRouteQuery(1))
  await loadCollection(1)
}

async function navigateCollection(page: number) {
  await replaceCollectionRoute(collectionRouteQuery(page))
  await loadCollection(page)
}

watch(
  () => route.query,
  async (query) => {
    const key = collectionRouteKey(query)
    if (key === handledCollectionRouteKey.value) return
    handledCollectionRouteKey.value = key
    if (String(query.tab || '') !== 'collection') {
      activeTab.value = experiment.value?.status === 'draft' ? 'definition' : 'tasks'
      return
    }
    activeTab.value = 'collection'
    collectionClassId.value = String(query.class_id || '')
    collectionStatus.value = String(query.status || '')
    collectionQuery.value = String(query.q || '')
    await loadCollection(normalizedCollectionPage(query.page))
  },
  { deep: true },
)

function collectionTone(status: string) {
  if (status === 'completed') return 'success' as const
  if (status === 'in_progress') return 'warning' as const
  if (status === 'expired') return 'danger' as const
  return 'neutral' as const
}
```

In `load()`, replace the current unconditional tab assignment with this route-aware assignment after the definition is loaded:

```typescript
const requestedTab = String(route.query.tab || '')
handledCollectionRouteKey.value = collectionRouteKey(route.query)
activeTab.value = requestedTab === 'collection'
  ? 'collection'
  : definition.status === 'draft' ? 'definition' : 'tasks'
```

After the class progress loop completes, add:

```typescript
if (activeTab.value === 'collection') {
  await loadCollection(normalizedCollectionPage(route.query.page))
}
```

This preserves lazy loading on normal detail-page entry while restoring the collection tab and filters on reload. Replace the three tab click handlers with `selectTab(...)`.

- [ ] **Step 2: Render complete class totals and the student detail**

Replace the collection panel with a block that:

```vue
<section v-else id="experiment-panel-collection" role="tabpanel" aria-labelledby="experiment-tab-collection">
  <section class="kt-card kt-published-task-table" aria-labelledby="collection-status-title">
    <div class="kt-section-heading"><div><p class="kt-kicker">采集状态</p><h2 id="collection-status-title">班级任务进度</h2></div><span>{{ tasks.length }} 个</span></div>
    <EmptyState v-if="!tasks.length" title="暂无采集任务"><p>发布到班级后，服务端会在这里返回各任务的采集进度。</p></EmptyState>
    <div v-else class="kt-table-scroll" data-collection-summary><table class="kt-table"><thead><tr><th>班级</th><th>状态</th><th>参与者</th><th>未开始</th><th>进行中</th><th>正常完成</th><th>已超时</th><th>已作废</th></tr></thead><tbody><tr v-for="task in tasks" :key="task.id"><td>{{ task.class_name }}</td><td>{{ task.business_status }}</td><td>{{ progress[task.id]?.total_participants ?? task.participant_count }}</td><td>{{ progress[task.id]?.not_started ?? '—' }}</td><td>{{ progress[task.id]?.in_progress ?? '—' }}</td><td>{{ progress[task.id]?.completed ?? '—' }}</td><td>{{ progress[task.id]?.expired ?? '—' }}</td><td>{{ progress[task.id]?.invalidated ?? '—' }}</td></tr></tbody></table></div>
  </section>

  <section class="kt-card kt-table-card" data-collection-participants aria-labelledby="collection-participant-title">
    <div class="kt-section-heading"><h2 id="collection-participant-title">学生状态明细</h2><span>{{ collection?.page.total_items ?? 0 }} 人</span></div>
    <form class="kt-class-filter" data-collection-filter @submit.prevent="applyCollectionFilters">
      <label>班级<select v-model="collectionClassId" data-collection-class><option value="">全部班级</option><option v-for="choice in collection?.class_choices || []" :key="choice.id" :value="String(choice.id)">{{ choice.name }}</option></select></label>
      <label>搜索学生<input v-model="collectionQuery" data-collection-query type="search" placeholder="姓名或学号"></label>
      <label>状态<select v-model="collectionStatus" data-collection-status><option value="">全部状态</option><option v-for="choice in collection?.status_choices || []" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label>
      <div class="kt-inline-actions"><Button type="submit">应用筛选</Button><button class="kt-button kt-button--secondary" type="button" @click="clearCollectionFilters">清除筛选</button></div>
    </form>
    <Alert v-if="collectionError" tone="danger">{{ collectionError }}</Alert>
    <p v-else-if="collectionLoading" role="status">正在加载学生采集状态…</p>
    <EmptyState v-else-if="!collection?.items.length" title="当前筛选条件下没有学生"><p>请选择其他条件或清除筛选。</p><button class="kt-button kt-button--secondary" data-collection-clear type="button" @click="clearCollectionFilters">清除筛选</button></EmptyState>
    <template v-else>
      <div class="kt-table-scroll kt-desktop-only"><table class="kt-table"><thead><tr><th>学生</th><th>班级</th><th>状态</th><th>开始时间</th><th>结束时间</th><th>提交题数</th></tr></thead><tbody><tr v-for="student in collection.items" :key="student.participant_id"><td><strong>{{ student.display_name }}</strong><span class="kt-cell-note">{{ student.student_number }}</span></td><td>{{ student.class_name }}</td><td><StatusBadge :tone="collectionTone(student.status)" :label="student.status_label" /></td><td>{{ collectionDate(student.started_at) }}</td><td>{{ collectionDate(student.ended_at) }}</td><td>{{ student.submission_count }}</td></tr></tbody></table></div>
      <MobileCardList class="kt-mobile-only"><li v-for="student in collection.items" :key="student.participant_id"><div class="kt-section-heading"><strong>{{ student.display_name }}</strong><StatusBadge :tone="collectionTone(student.status)" :label="student.status_label" /></div><p>{{ student.student_number }} · {{ student.class_name }}</p><p>开始：{{ collectionDate(student.started_at) }} · 结束：{{ collectionDate(student.ended_at) }} · 提交 {{ student.submission_count }} 题</p></li></MobileCardList>
      <Paginator :page="collection.page.number" :total-pages="collection.page.total_pages" @navigate="navigateCollection" />
    </template>
  </section>
</section>
```

Inside the empty state, add a secondary button with `data-collection-clear` that calls `clearCollectionFilters`, so the user can recover without editing each control. Keep `Alert`, loading, empty, and result branches mutually exclusive (`v-if` / `v-else-if` / `v-else`). The collection-specific date formatter above preserves the approved `—` display and avoids reusing `displayDate()`, which returns “未设置”.

- [ ] **Step 3: Run the frontend tests**

```powershell
npm --prefix frontend run test:unit -- TeacherExperimentDetail.test.ts
```

Expected: all `TeacherExperimentDetail` tests PASS.

- [ ] **Step 4: Add explicit reload, clear, empty, error, and mobile assertions**

Extend the same test file with dedicated tests that set `mocks.route.query` before mounting to prove reload restoration, submit and then clear filters to prove query reset, and make `collectionParticipants` reject once after an earlier successful result to prove stale rows disappear. Include these concrete assertions:

```typescript
expect(mocks.api.collectionParticipants).toHaveBeenCalledWith(11, {
  classId: 5, status: 'expired', query: 'S019', page: 2,
})
expect(mocks.replace).toHaveBeenLastCalledWith({ query: { tab: 'collection' } })
expect(wrapper.get('[data-collection-participants]').text()).toContain('当前筛选条件下没有学生')
expect(wrapper.get('[data-collection-clear]').exists()).toBe(true)
expect(wrapper.get('.kt-mobile-only').text()).toContain('提交 2 题')
expect(wrapper.find('[role="alert"]').text()).toContain('学生采集状态加载失败')
expect(wrapper.text()).not.toContain('超时学生')
```

Run the same test command and require PASS.

- [ ] **Step 5: Commit the frontend implementation**

```powershell
git add frontend/src/features/teacher-experiments/TeacherExperimentDetail.vue frontend/src/features/teacher-experiments/TeacherExperimentDetail.test.ts
git commit -m "feat: filter single experiment collection status"
```

### Task 6: Run scoped regression and visual verification

**Files:**
- Verify only; modify files only for concrete failures caused by this feature.

- [ ] **Step 1: Run the targeted backend suite**

```powershell
.\.venv\Scripts\python.exe manage.py test apps.experiments.test_api.TeacherPublishPlanApiTests
```

Expected: PASS with zero failures.

- [ ] **Step 2: Run backend configuration and schema checks**

```powershell
.\.venv\Scripts\python.exe manage.py check
npm --prefix frontend run api:check
```

Expected: both commands exit 0.

- [ ] **Step 3: Run frontend lint, type, unit, and production build checks**

```powershell
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test:unit -- TeacherExperimentDetail.test.ts
npm --prefix frontend run build
```

Expected: all commands exit 0; Vitest reports zero failed tests.

- [ ] **Step 4: Verify the rendered page visually**

Start the normal local Django/Vite preview with the project-scoped PostgreSQL and Playwright browser paths, sign in as a teacher with a published experiment, open the experiment’s “采集状态” tab, and save screenshots for desktop and mobile widths. Confirm all of the following from the rendered UI:

- class totals show all five states and reconcile to participants;
- the student list is absent from initial non-collection tabs and loads on collection entry;
- class, text, and status filters combine correctly;
- an unstarted student shows `—`, `—`, and `0`;
- expired and invalidated badges are distinct;
- pagination and URL state survive reload;
- the mobile cards contain the same student/class/status/submission facts.

Expected: no overflow, clipped controls, console errors, failed API requests, or state mismatch. Record the screenshot paths in the implementation handoff; do not claim production UAT.

- [ ] **Step 5: Inspect the final diff and repository state**

```powershell
git diff --check
git status --short
git log --oneline --decorate -6
```

Expected: `git diff --check` has no output; the worktree is clean; only the planned commits follow the approved documentation commits.

- [ ] **Step 6: Request code review before integration**

Use the `requesting-code-review` skill against the branch diff. Address only concrete findings within this feature scope, rerun the affected targeted checks, and keep GitHub/Gitee push plus Tencent Cloud deployment as a separate explicitly authorized release step.
