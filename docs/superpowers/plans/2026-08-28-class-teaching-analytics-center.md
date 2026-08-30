# 班级教学分析中心与学生“我的学习”实施计划 v1.0

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有班级名单页升级为唯一的日常教学分析中心，并在教师可见性策略控制下，为学生提供只包含本人数据和受保护班级基准的“我的学习”。

**Architecture:** 保留 Django 5.2 单体架构，以不可变发布快照、班级快照、参与者快照、正式提交和当前有效评分为唯一事实链。教师和学生共用同一组只读统计原语，但学生 DTO 必须由 allow-list 正向构造；常规页面按请求聚合，过程数据只在深钻页面按需加载，不引入物化统计、轮询或前端统计计算。

**Tech Stack:** Django 5.2、Django Templates、PostgreSQL（正式性能目标）、SQLite（功能开发）、Bootstrap 5.3、本地 CSS、原生 JavaScript、原生 SVG。

---

## 0. 文档状态与本次修订

本文件是替换上一版计划的完整实施基线。以下五项已经进入冻结契约，不再作为开放问题：

1. Phase 1 只交付班级级页面；学生详情、单题详情和过程重放全部移动到 Phase 2。
2. 教师概览的 pooled accuracy 统一命名为“总体正确率”，不得再称“平均正确率”。
3. 学生比较的“班级平均正确率”单独定义为学生个人正确率的算术平均，即 student-level macro-average。
4. `numerator/denominator` 表示指标观测计数；`sample_size` 只表示贡献比较统计的独立学生人数。
5. 数值题和短填空可在教师重放中显示实际状态值链；长文本不显示中间正文。普通业务文档统一使用“不可变实验发布快照”，当前技术实现映射至 `ExperimentVersion`。

除上述修订外，上一版已经冻结的权限边界、事实链、历史冲突隔离、KC 全映射、单实验排名和分阶段上线原则保持不变。

---

## 1. 产品目标、信息架构与非目标

### 1.1 教师端唯一日常入口

班级详情页成为唯一的日常教学分析入口：

```text
班级中心
├─ 概览
├─ 学生表现
├─ 实验分析
└─ 班级管理
```

课程“数据中心”只保留：

- 科研数据范围与数据字典；
- 数据质量；
- 匿名、导航、实名教学成绩等既有导出；
- 原始事件、快照和高级工具。

不得在数据中心继续维护第二套日常教学统计页面、查询服务或公式。

### 1.2 教师端下钻层级

```text
班级级（Phase 1）
概览 → 学生表现列表 → 实验列表 → 单实验概要

个体/题目级（Phase 2）
学生详情 → 某学生某实验 → 单题详情 → 某学生该题过程重放
```

Phase 1 的学生表可以展示每名当前学生的汇总事实，但学生姓名/学号不得链接到尚未开放的 Phase 2 路由。Phase 1 的单实验概要可以展示非交互式题目正确率摘要行，但选项分布、首答/终答四分类、个体名单和过程链必须留到 Phase 2。

### 1.3 学生端信息架构

```text
课程
├─ 实验任务（保留为默认入口）
└─ 我的学习（Phase 3）
   ├─ 概览
   ├─ 分析
   └─ 结果回顾
```

学生只能看到：

- 本人的参与、提交和完成状态；
- 教师与实验反馈模式共同允许的本人成绩、正确率和逐题结果；
- 满足样本量规则的班级统计基准；
- 单实验本人位次；
- 本人的 KC 正确率和跨实验趋势。

学生不得看到其他学生姓名、学号、逐题答案、过程轨迹或完整排行榜。

### 1.4 明确不纳入 v1

- 全课程综合排名或综合能力分；
- 完整实名排行榜；
- KT/CD 模型结果的普通教师或学生展示；
- “掌握度、能力、犹豫、粗心、认知冲突”等模型或心理推断；
- 延迟公布结果、课堂实时监控、自动轮询、消息通知；
- 新的人工评分工作流；
- 班级中心导出按钮；
- 学生过程重放；
- 物化统计表、后台聚合任务或缓存失效体系；
- React、Vue、HTMX、Tailwind、GSAP、npm、远程字体或新图表库。

---

## 2. 业务术语与内部模型映射

普通教师、学生、产品文案和架构叙述使用以下术语：

| 业务术语 | 当前内部实现 | 普通 UI 是否出现内部名 |
|---|---|---|
| 实验 | `Experiment` | 否 |
| 不可变实验发布快照 | `ExperimentVersion` | 否 |
| 不可变班级快照 | `ExperimentClassSnapshot` | 否 |
| 冻结参与资格 | `ExperimentVersionParticipantSnapshot` | 否 |
| 班级实验任务 | 新增 `ClassExperimentTask` | 是，仅中文业务名 |
| 实验作答 | `ExperimentSession` | 否 |
| 题目作答 | `Attempt` | 否 |
| 正式提交 | `SubmissionEvent` | 否 |
| 当前有效评分 | effective `GradeRecord` | 否 |

冻结规则：

- 普通页面不得出现 Version、latest、release batch、Session、Attempt、snapshot UUID 等技术词。
- Django 模型 `ExperimentVersion` 不改名，不做破坏性迁移。
- 新代码字段使用 `release_snapshot` 表达业务含义，其外键目标是 `ExperimentVersion`。
- 调试日志、管理命令 JSON 和高级工具可以使用技术模型名，但必须与普通页面隔离。

---

## 3. 权威事实链与研究不变量

### 3.1 权威数据来源

```text
当前班级成员      active CourseEnrollment
应参与资格        ExperimentVersionParticipantSnapshot
班级任务          ClassExperimentTask
实验状态          ExperimentSession
题目分配          StudentQuestionAssignment
正式提交          SubmissionEvent
当前评分          validity_status=effective 的最终 GradeRecord
题目与 KC         QuestionSnapshot + 去重后的 all_kcs
过程状态          AssessmentEvent + ResponseSnapshot + ResponseInputObservation
```

任何班级成员全集不得从 Session、Attempt、Submission 或 GradeRecord 反推。

### 3.2 不得破坏的研究规则

- 成功进入题目后才能更新 visit；保留首次打开与末次打开的服务端时间语义。
- 草稿、状态快照或自动保存不得创建正式提交。
- 每题只允许一个有效正式提交；提交成功时间取数据库事务成功时间。
- 正式提交后答案锁定。
- 分配顺序、首次访问顺序和提交顺序仍是三个独立序列。
- 跳过、未答和超时不得自动记错或记零分。
- `feedback_mode=none` 不得泄露正误、标准答案或解析。
- 发布后题库修改不得改变既有快照。
- 学生分析页面不得改变 CSRF、授权、幂等、事务、事件或导出语义。

---

## 4. 数据模型与迁移契约

### 4.1 `ClassExperimentTask`

**文件：**

- 修改：`apps/experiments/models.py`
- 修改：`apps/experiments/services.py`
- 修改：`apps/experiments/admin.py`
- 创建：`apps/experiments/migrations/0011_class_experiment_task.py`
- 创建：`apps/experiments/test_class_experiment_tasks.py`

建议模型契约：

```python
class ClassExperimentTask(models.Model):
    class_group = models.ForeignKey(
        "accounts.ClassGroup",
        on_delete=models.PROTECT,
        related_name="experiment_tasks",
    )
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.PROTECT,
        related_name="class_tasks",
    )
    release_snapshot = models.ForeignKey(
        ExperimentVersion,
        on_delete=models.PROTECT,
        related_name="class_tasks",
    )
    class_snapshot = models.OneToOneField(
        ExperimentClassSnapshot,
        on_delete=models.PROTECT,
        related_name="class_task",
    )
    published_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["class_group", "experiment"],
                name="unique_class_experiment_task",
            ),
        ]
        indexes = [
            models.Index(
                fields=["class_group", "-published_at"],
                name="class_task_class_pub_idx",
            ),
        ]
```

模型校验必须同时验证：

- `experiment.course_id == class_group.course_id`；
- `release_snapshot.experiment_id == experiment_id`；
- `class_snapshot.version_id == release_snapshot_id`；
- `class_snapshot.source_class_group_id == class_group_id`；
- `published_at == release_snapshot.published_at`。

不可变性必须覆盖：

- 实例更新；
- QuerySet `update()`；
- 普通业务删除；
- Django Admin 编辑和删除。

允许的唯一创建入口是发布事务或历史回填迁移。课程归档、班级停用、实验关闭或归档均不得释放 `class_group × experiment` 业务键。

### 4.2 发布事务

`publish_experiment()` 在同一 `transaction.atomic()` 中依次完成：

1. 锁定实验及参与规则；
2. 再次验证课程、实验与班级均可发布；
3. 创建不可变实验发布快照；
4. 创建题目、班级和参与者快照；
5. 为每个发布班级创建 `ClassExperimentTask`；
6. 更新实验发布状态；
7. 写入发布审计。

任何任务唯一约束失败必须回滚该次发布产生的全部快照、任务和状态更新。不得捕获 `IntegrityError` 后保留半成品。PostgreSQL 并发测试必须证明同一业务任务只能有一个成功提交。

### 4.3 历史回填与冲突隔离

迁移仅在以下条件全部满足时回填任务：

- `ExperimentClassSnapshot.source_class_group_id` 非空；
- 班级与实验属于同一课程；
- 同一 `class_group × experiment` 只关联一个 distinct `ExperimentVersion`；
- 该版本中只存在一个匹配班级快照。

以下情况一律不推断、不回填：

- 同一班级和实验出现多个发布快照；
- 只有历史班级文字标签而没有稳定 `source_class_group_id`；
- 班级与实验课程关系不一致；
- 版本、班级快照或参与者快照缺失/重复。

新增只读命令：

```powershell
python manage.py audit_class_experiment_tasks
python manage.py audit_class_experiment_tasks --format json
```

命令输出至少包含：course、class、experiment、发布快照数、班级快照数、参与者数、Session 数、Attempt 数、GradeRecord 数和冲突原因。不得输出学生姓名或答案。

普通班级页对可定位到当前班级的冲突组显示“历史发布冲突”，并隔离所有完成率、正确率、得分率、KC、比较和排名。无法稳定映射到现班级的遗留标签只进入高级审计结果。

### 4.4 `ClassStudentAnalyticsPolicy`

**文件：**

- 修改：`apps/accounts/models.py`
- 创建：`apps/accounts/student_analytics_policy.py`
- 修改：`apps/accounts/admin.py`
- 创建：`apps/accounts/migrations/0008_class_student_analytics_policy.py`
- 创建：`apps/accounts/test_student_analytics_policy.py`

字段：

```python
class ClassStudentAnalyticsPolicy(models.Model):
    class RankMode(models.TextChoices):
        NONE = "none", "不显示"
        SELF = "self", "仅显示本人位置"

    class_group = models.OneToOneField(
        ClassGroup,
        on_delete=models.PROTECT,
        related_name="student_analytics_policy",
    )
    show_scores = models.BooleanField(default=True)
    show_correct_rate = models.BooleanField(default=True)
    show_item_results = models.BooleanField(default=True)
    show_class_average = models.BooleanField(default=True)
    rank_mode = models.CharField(
        max_length=8,
        choices=RankMode.choices,
        default=RankMode.SELF,
    )
    show_kc_rates = models.BooleanField(default=True)
    show_trend = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.PROTECT,
        related_name="updated_student_analytics_policies",
    )
    updated_at = models.DateTimeField(auto_now=True)
```

保存服务必须规范化依赖：

- `show_correct_rate=False` 时强制 `show_kc_rates=False`、`show_trend=False`；
- `show_scores=False` 时强制 `rank_mode=none`；
- 重新打开上游开关不得自动恢复下游旧值，由教师明确选择；
- 课程负责人、协作教师、管理员可写；归档课程只读；
- 写入使用 CSRF、`select_for_update()` 和事务；
- 审计 details 保存 before/after，不保存学生数据；
- 普通 GET 不写访问审计。

Phase 0 只建立模型与服务；策略设置 UI 和学生读路径统一在 Phase 3 开放，避免提前交付无实际消费者的控制界面。

### 4.5 Feature flags

修改 `config/settings.py`：

```python
CLASS_CENTER_ENABLED = os.getenv("CLASS_CENTER_ENABLED", "0") == "1"
STUDENT_LEARNING_ANALYTICS_ENABLED = (
    os.getenv("STUDENT_LEARNING_ANALYTICS_ENABLED", "0") == "1"
)
```

依赖关系：学生旗标只有在教师旗标打开时才生效。应用回滚只关闭旗标和入口，不回滚或删除加法式迁移。

---

## 5. 分析服务、DTO 与一致读取契约

### 5.1 文件结构

创建 `apps/analytics/teaching/`：

```text
teaching/
├─ __init__.py
├─ contracts.py           DTO、枚举、排除原因、显示状态
├─ scope.py               ClassAnalysisScope 与事实范围
├─ metrics.py             班级/学生/实验/题目核心指标
├─ comparisons.py         学生级宏平均、完整可比集合、竞赛排名
├─ kc_time.py             KC、分位数、时长与质量构成
├─ process.py             题型相关过程派生与教师语义化重放
└─ student_projection.py  可见性求交与 Student allow-list DTO
```

旧 `apps/analytics/teaching_data.py` 在 Phase 4 前仅作为兼容层存在；新页面不得调用它。

### 5.2 DTO 计数语义

核心 DTO：

```python
@dataclass(frozen=True)
class MetricValue:
    numerator: int | Decimal | None
    denominator: int | Decimal | None
    raw_value: Decimal | None
    display_value: str | None
    observation_unit: Literal[
        "responses", "student_tasks", "sessions", "students", "seconds"
    ]
    sample_size: int | None
    excluded_count: int
    data_state: DataState
    exclusion_reasons: tuple[ExclusionReason, ...]
    generated_at: datetime
```

冻结定义：

- `numerator/denominator` 是公式本身的观测计数或量值。
- `sample_size` 是贡献该统计的 distinct student 数；不是所有指标都需要显示，但含义永远不变。
- 不涉及学生贡献者或无法可靠计算时，`sample_size=None`，不得拿 denominator 填充。
- `observation_unit` 必须随 DTO 返回，模板不得猜测分母单位。
- 0 是有效值；只有 denominator 为 0/空或被权限禁止时才没有指标。

展示示例：

```text
KVL 正确率 75.0%
正确作答 15 / 有效作答 20
涉及学生 12 人
```

学生班级比较 DTO：

```python
@dataclass(frozen=True)
class StudentComparison:
    student_value: Decimal | None
    class_mean: Decimal | None
    sample_size: int
    minimum_sample_size: int
    data_state: DataState
    generated_at: datetime
```

这里的 `sample_size` 只能是进入宏平均的独立学生人数。

### 5.3 一致读取

每个普通统计 GET 必须：

1. 完成身份与对象范围校验；
2. 进入一个只覆盖统计读取的事务；
3. PostgreSQL 执行 `REPEATABLE READ, READ ONLY`；SQLite 保持功能兼容；
4. 在事务内生成唯一 `generated_at`；
5. 所有 DTO 使用同一 `generated_at`；
6. 退出统计事务后再渲染模板。

模板渲染、过程重放分页和管理写操作不得被包进同一个长事务。不得使用前端重新请求各卡片拼接一次页面。

### 5.4 范围对象

```python
@dataclass(frozen=True)
class ClassAnalysisScope:
    course: Course
    class_group: ClassGroup
    experiment_task: ClassExperimentTask | None = None
```

默认全部实验范围只纳入：

- 存在无冲突 `ClassExperimentTask`；
- 实验状态为 published 或 closed；
- 非 archived、非 draft；
- 任务、发布快照和班级快照相互一致。

页面统一范围选择：

```text
全部实验 | 单个实验
```

范围选择必须同时影响四指标、分布、状态、KC、学生表、实验表和比较统计；不得出现卡片仍是全部范围、表格却是单实验的混合页面。

---

## 6. 统计公式与状态契约

### 6.1 教师“总体正确率”：作答级微平均

教师班级概览与单实验概要统一显示：

> **总体正确率**

公式：

```text
有效 GradeRecord 中 is_correct=True 的记录数
÷
有效 GradeRecord 中 is_correct 非空的记录数
```

其中“有效 GradeRecord”同时要求：

- `validity_status=effective`；
- `status=completed`；
- 对应唯一正式提交；
- 所属 Session、任务和范围无冲突且未作废。

未提交、待评分、重评分等待、candidate、superseded、voided 和 `is_correct=NULL` 均不进入分母。每个 GradeRecord 只计一次，即使题目映射多个 KC。

必须用以下夹具防止退化为宏平均：

```text
学生 A：1/1 = 100%
学生 B：5/10 = 50%

教师总体正确率 = 6/11 = 54.545...%
不是 75%
```

### 6.2 学生“班级平均正确率”：学生级宏平均

学生本人的正确率先按同一分析范围计算：

```text
个人有效正确记录数 ÷ 个人有效已评分记录数
```

班级平均正确率再计算：

```text
所有具有个人有效正确率的可比较学生个人正确率之和
÷
可比较学生人数
```

每名学生权重相等，不按答题数加权。上述 A/B 例中：

```text
班级平均正确率 = (100% + 50%) / 2 = 75%
sample_size = 2 人
```

范围定义：

- 单实验结果：同一 `ClassExperimentTask` 的冻结参与者集合，包括后来离班但属于该实验可比 cohort 的历史参与者；
- 课程级“我的学习”：当前 active 班级 roster 中的学生，在同一班级全部可见且无冲突任务范围内，先分别计算个人 pooled accuracy，再对学生做 macro-average；
- KC 比较：每名学生先计算该 KC 的个人正确率，再做 macro-average；
- 本人包含在班级平均中；
- 每项指标独立形成有效比较集合和 `sample_size`；
- `sample_size < 5` 时不返回班级平均，只返回“样本不足，暂不显示班级比较”。

其他学生班级基准沿用同一“先个人、后班级”的宏平均原则：

- 班级平均得分率：每名学生先计算个人有效 `sum(score)/sum(max_score)`，再对具有完整可比结果的学生等权平均；
- 班级平均完成率：每名学生先计算本人 `completed assigned tasks / assigned tasks`，再对具有至少一个应参与任务的学生等权平均；
- 每项基准使用自己的可比较集合和 `sample_size`，不得为了页面整齐强迫正确率、得分率和完成率共用人数。

教师概览的“总体正确率”与学生比较的“班级平均正确率”不得复用同一服务函数或 DTO 字段。

### 6.3 其他核心指标

| 指标 | 冻结公式 | 观测单位 |
|---|---|---|
| 班级人数 | 当前 active `CourseEnrollment` 数 | students |
| 已参与人数 | 当前成员中，范围内至少有一个有效或 expired Session 的 distinct student 数 | students |
| 已评分得分率 | 成对非空的 effective `score` 总和 ÷ 对应 `max_score` 总和 | responses/score points |
| 实验完成率 | completed 的当前成员-任务对 ÷ 当前成员中冻结应参与的任务对 | student_tasks |
| 题目正确率 | 题目 effective correct 数 ÷ effective `is_correct` 非空数 | responses |
| KC 正确率 | 映射至该 KC 的 effective correct 贡献数 ÷ effective evaluated 贡献数 | responses |
| 完成总历时 | completed Session 的 `completed_at-started_at` 中位数 | sessions/seconds |
| 题目有效时长 | active 或 estimated-active 题目时长中位数 | responses/seconds |

顶部卡片中文固定为：

```text
班级人数 | 已参与人数 | 总体正确率 | 实验完成率
```

### 6.4 得分、覆盖度和排名

- 得分率只使用 `score` 与 `max_score` 同时非空的 effective GradeRecord。
- 不把缺失评分补零；明确有效零分必须保留。
- 部分评分显示“已评分得分率”和“已评分 x/y 题”，标记“部分结果”。
- 部分结果可以出现在教师列表，不进入排名和完整学生比较。

排名仅在单实验范围存在，资格必须同时满足：

- 冻结参与者；
- Session 正常 completed，非 expired、invalidated；
- 每个已分配计分题均有正式提交；
- 每个提交均有最终 effective completed GradeRecord；
- 每个评分的 `score/max_score` 成对非空且 `max_score>0`。

排序使用未舍入的精确 Decimal 得分率，降序采用竞赛排名 `1,2,2,4`。不得以作答时间、学号、姓名或提交先后破同分。

排名 DTO 分开返回：

```text
rank             本人名次
ranked_count     满足完整排名资格的人数
assigned_count   冻结应参与人数
```

不得把 `ranked_count` 模糊标成 N。学生本人位次还要求 `rank_mode=self`、`show_scores=true`、实验 feedback 允许结果，并且 `ranked_count>=5`。v1 不返回完整排行榜。

### 6.5 KC

- 使用 `QuestionSnapshot.all_kcs`，先去重、去空白；不只使用 primary KC。
- 一个有效 GradeRecord 对题目每个冻结 KC 各贡献一次。
- 同一题的重复 KC 标签不得重复贡献。
- 各 KC 的 numerator/denominator 独立，不能跨 KC 相加。
- 教师总体正确率仍按 GradeRecord 一次计数。
- `denominator>=1` 显示百分比与正确/有效作答数；`denominator=0` 显示暂无数据。
- `sample_size` 是至少贡献一个有效作答的 distinct student 数。
- 小样本只加事实性说明，不生成“薄弱”“掌握不足”等推断。

### 6.6 时长与分位数

题目时长只纳入：

- exact active duration；
- synchronized estimated active duration。

只有 wall-clock 的题目记录不进入题目时长中位数，但计入质量构成的排除数。详情展示中位数、IQR、有效记录数和 exact/estimated/excluded 数。

分位数统一使用线性插值 Type 7：排序后 `h=(n-1)p`，在相邻值间插值；`n=1` 时 Q1=median=Q3。不得由数据库、Python 模板和 JavaScript分别使用不同算法。

实验顶部“完成总历时”只使用正常 completed Session 的 `completed_at-started_at`，显示中位数与 Session 数，并明确它不是有效学习时间。题目有效时长总和仅在深钻详情另列。

### 6.7 状态

单实验：

```text
已完成 | 进行中 | 未参与 | 未分配 | 已超时/未完成 | 不可评估
```

全部实验：

```text
全部完成 | 部分完成 | 尚未开始 | 未分配 | 不可评估
```

规则：

- expired 计已参与和完成率分母，不计完成率分子；全部范围归“部分完成”并附超时标记。
- invalidated 和冲突不进入指标，显示排除数与“不可评估”。
- 当前 roster 学生未在任务参与快照中为“未分配”。
- 已分配但无有效 Session 为“未参与”。
- 已离开当前 roster 的冻结参与者不进入当前班级人数和当前学生列表，但在相关单实验页单列“历史参与者”。

---

## 7. 过程派生与隐私契约

### 7.1 通用链规则

- 事件按 `server_ingest_sequence_no` 排序；时间只作为展示，不作为重排依据。
- `student_state_transition_count` 只计 `change_source=student` 且相邻逻辑状态不同的变化。
- restore、forced snapshot、autosave、重复同状态和 submit 不计状态修改。
- 状态引用必须解析到同一 Attempt 的 `ResponseSnapshot`；跨 Attempt、缺失引用或哈希不一致标为“不完整链”，不得猜测。

### 7.2 选择题

- 单选/判断：首次 `null→answer` 是首答，不算修改；后续不同答案每次计一次。
- 多选：首次集合构建和只新增从未选过的选项不算修改；删除已选项或重新选择曾删除项时，每个状态事件最多计一次。
- “修改过答案人数”按上述题型规则派生，不以事件数大于 1 替代。
- 首答正确×终答正确四分类只用于单选和判断，且必须具有完整状态链、正式提交和 effective 评分。

### 7.3 数值与填空

当前 `ResponseSnapshot.state_json/content` 保存了不可变状态，因此教师可展示真实状态链，例如：

```text
12 → 120 → 12.5 → 提交 12.5
```

规则：

- 展示学生实际输入的词法状态，不以评分归一化结果替换；
- HTML 必须转义；
- 仅解析属于同一 Attempt 的不可变快照；
- 不生成与选择题可横向比较的“修改次数”；
- 可同时显示 insert/delete/replace/paste、长度变化和时间；
- 无法解析快照时只显示编辑事实，不从 hash 反推内容。

### 7.4 长文本简答

普通教师页仅显示：

```text
开始输入
插入，字数 0→18
删除，字数 18→12
替换 / 粘贴
提交
最终答案：……
```

不显示全部中间正文，不显示 UUID、hash、raw payload、客户端实例 ID 或技术质量字段。最终答案来自正式提交绑定的最终 ResponseSnapshot/SubmissionEvent。

### 7.5 访问边界

- 教师语义化重放：Phase 2 普通教师页；
- 原始事件、快照和技术字段：数据中心高级工具；
- 学生端：不返回过程链、修改次数或编辑事实。

---

## 8. 学生可见性矩阵

最终可见字段在服务端计算：

```text
班级策略
∩ Experiment.feedback_mode
∩ 数据可用性
∩ 样本量规则
```

### 8.1 feedback 模式

| 模式 | 学生始终可见 | 条件可见 | 永远不可见 |
|---|---|---|---|
| none | 参与、提交、完成状态 | 无结果性指标 | 正误、成绩、正确率、KC、趋势、比较、排名、标准答案、解析 |
| correctness | 参与、提交、完成状态 | 本人成绩、正确率、逐题结果、本人最终答案、KC、趋势、班级平均、本人位次 | 标准答案、解析、他人数据、过程 |
| full | 参与、提交、完成状态 | correctness 全部字段、标准答案、解析 | 他人数据、过程、完整榜单 |

### 8.2 策略依赖

- `show_scores` 控制本人成绩/得分率；关闭时排名必为 none。
- `show_correct_rate` 控制本人正确率；关闭时 KC 和正确率趋势必关闭。
- `show_item_results` 控制逐题正误与本人最终答案，但标准答案/解析还需 full。
- `show_class_average` 只控制达到 `sample_size>=5` 的班级宏平均。
- `rank_mode=self` 只控制单实验本人位次，不产生排行榜。
- `show_kc_rates` 控制本人 KC 正确率及允许时的 KC 班级宏平均。
- `show_trend` 只控制跨实验正确率趋势；有效实验少于 2 个时改为列表，不画伪趋势。

隐藏内容必须从 Student DTO、模板上下文和 HTML 中完全消失；不能用 CSS 隐藏，也不能用“—”占位。有效对象属于本人但策略禁止的直接详情 URL 返回 403 中性说明页；跨课程、跨班、跨学生或猜测对象 ID 返回 404。

---

## 9. 分阶段实施与严格门禁

## Phase 0：数据契约、迁移、唯一任务与策略基础

### Task 0.1：建立基线并验证迁移前提

**Files:**

- Read: `apps/experiments/migrations/0010_data_center_foundation.py`
- Read: `apps/assessment/migrations/0018_data_center_foundation.py`
- Read: `apps/assessment/migrations/0019_response_snapshot_state_hash_uniqueness.py`
- Read: `apps/analytics/migrations/0006_export_tombstones_and_scope.py`

- [ ] 在数据库副本运行 `showmigrations`，记录未应用迁移。
- [ ] 备份测试副本后应用现有迁移，不在未验证副本上直接创建 0011/0008。
- [ ] 运行现有 29 个教学/快照/评分契约测试，确认基线通过。
- [ ] 运行 `python manage.py check` 与 `makemigrations --check --dry-run`。

### Task 0.2：测试先行实现唯一班级实验任务

**Files:**

- Create: `apps/experiments/test_class_experiment_tasks.py`
- Modify: `apps/experiments/models.py`
- Modify: `apps/experiments/services.py`
- Create: `apps/experiments/migrations/0011_class_experiment_task.py`

- [ ] 先写同班同实验重复失败、同实验跨班成功、不可更新/删除、跨课程/错快照失败测试。
- [ ] 运行测试并确认因模型不存在而失败。
- [ ] 实现最小模型、约束和不可变 manager。
- [ ] 接入发布事务并写并发测试。
- [ ] 运行 experiments 全量测试。

### Task 0.3：回填与冲突审计

**Files:**

- Create: `apps/experiments/class_task_audit.py`
- Create: `apps/experiments/management/commands/audit_class_experiment_tasks.py`
- Create: `apps/experiments/test_class_task_audit.py`

- [ ] 为唯一历史版本、重复版本、空 source class、跨课程异常分别写夹具。
- [ ] 断言唯一组回填，歧义组原样保留且不创建任务。
- [ ] 断言表格/JSON 输出不含学生身份和答案。
- [ ] 验证命令重复运行只读且输出稳定。

### Task 0.4：策略模型与审计写服务

**Files:**

- Create: `apps/accounts/test_student_analytics_policy.py`
- Modify: `apps/accounts/models.py`
- Create: `apps/accounts/student_analytics_policy.py`
- Create: `apps/accounts/migrations/0008_class_student_analytics_policy.py`

- [ ] 测试默认值、依赖规范化、写权限、归档只读、行锁和 before/after 审计。
- [ ] 为已有班级回填默认策略。
- [ ] 不在 Phase 0 增加教师设置 URL。

### Task 0.5：旗标与发布审计

**Files:**

- Modify: `config/settings.py`
- Modify: `apps/experiments/services.py`
- Modify: `apps/experiments/tests.py`

- [ ] 增加两个默认关闭的环境旗标。
- [ ] 发布审计记录 course/class/experiment/task 和内部快照 ID；普通 UI 不消费内部 ID。
- [ ] 运行全量研究不变量测试。

**Phase 0 Gate：**

- SQLite 和 PostgreSQL 回填副本均通过；
- 冲突记录零删除、零合并、零重挂；
- PostgreSQL 双发布只有一次提交成功且无半成品；
- 所有现有迁移在升级副本验证；
- 全量现有测试通过后才能进入 Phase 1。

## Phase 1：教师班级级分析

### 允许开放的路由

```text
/<course_pk>/classes/<class_pk>/
/<course_pk>/classes/<class_pk>/students/
/<course_pk>/classes/<class_pk>/experiments/
/<course_pk>/classes/<class_pk>/experiments/<experiment_pk>/
/<course_pk>/classes/<class_pk>/manage/
```

### 明确禁止在 Phase 1 开放的路由

```text
.../students/<student_pk>/
.../students/<student_pk>/experiments/<experiment_pk>/
.../experiments/<experiment_pk>/questions/<snapshot_pk>/
.../process/
.../student-visibility/
```

### Task 1.1：DTO、范围与一致读取

**Files:**

- Create: `apps/analytics/teaching/contracts.py`
- Create: `apps/analytics/teaching/scope.py`
- Create: `apps/analytics/test_teaching_contracts.py`

- [ ] 测试 numerator/denominator/sample_size 单位不可混用。
- [ ] 测试全部/单实验范围同步过滤所有事实查询。
- [ ] 测试当前 roster 与冻结参与资格严格分离。
- [ ] 测试 PostgreSQL 同页 DTO 使用同一快照和 `generated_at`。

### Task 1.2：教师核心指标

**Files:**

- Create: `apps/analytics/teaching/metrics.py`
- Create: `apps/analytics/test_class_metrics.py`

- [ ] 用 A=1/1、B=5/10 夹具断言总体正确率为 6/11，而非 75%。
- [ ] 覆盖有效零分、部分评分、pending、regrade、voided、unsubmitted、expired、invalidated、conflict。
- [ ] 实现四指标、学生个人汇总、状态分布、正确率分箱和实验概要。
- [ ] 分箱使用未舍入个人正确率，包含“无数据”。

### Task 1.3：KC 与时长摘要

**Files:**

- Create: `apps/analytics/teaching/kc_time.py`
- Create: `apps/analytics/test_kc_time.py`

- [ ] 测试 all_kcs 去重、多 KC 独立贡献和总体只计一次。
- [ ] 测试 KC `15/20` 与 `sample_size=12` 分开返回。
- [ ] 测试 Type 7 中位数/IQR、wall-clock 排除和 Session 完成总历时。

### Task 1.4：学生列表排序与单实验排名

**Files:**

- Create: `apps/analytics/teaching/comparisons.py`
- Create: `apps/analytics/test_class_comparisons.py`

- [ ] 测试排名资格完整性、Decimal 未舍入排序和 `1,2,2,4`。
- [ ] 测试 `ranked_count` 与 `assigned_count` 分开。
- [ ] 测试全部实验范围不返回排名。
- [ ] 当前 roster 学生列表服务端排序和分页前完成排名计算。

### Task 1.5：班级中心视图和路由

**Files:**

- Create: `apps/accounts/class_center_views.py`
- Modify: `apps/accounts/course_urls.py`
- Modify: `apps/accounts/course_resource_views.py`
- Create: `apps/accounts/test_class_center_views.py`

- [ ] `class_detail` 在旗标关闭时保持旧页，在打开时成为概览。
- [ ] owner/collaborator/admin 可读；外部教师 404；错误课程/班级组合 404。
- [ ] 实现范围、搜索、状态筛选、白名单排序和分页参数校验。
- [ ] 单实验页可以显示题目摘要行，但题目代码不带详情链接。

### Task 1.6：教师模板与响应式布局

**Files:**

- Create: `templates/teacher/class_center/base.html`
- Create: `templates/teacher/class_center/overview.html`
- Create: `templates/teacher/class_center/students.html`
- Create: `templates/teacher/class_center/experiments.html`
- Create: `templates/teacher/class_center/experiment_overview.html`
- Create: `templates/teacher/class_center/manage.html`
- Modify: `templates/teacher/course_class_detail.html`
- Create: `static/css/class-center.css`
- Create: `static/js/class-center.js`

- [ ] 桌面 ≥768px 使用可排序表格；移动 <768px 使用紧凑卡片，不依赖普通横向滚动。
- [ ] 四指标固定使用“总体正确率”。
- [ ] 状态图使用 100% 横向堆叠条，并有文字/数量/百分比等价说明。
- [ ] KC 使用低到高水平条；图形用原生 SVG，统计值全部来自 DTO。
- [ ] 管理页迁移原 roster 功能：导入、转班、移出、密码重置；行操作进入 Bootstrap modal，不用 `alert/confirm`。
- [ ] 所有修改操作保持原 URL/form/CSRF 契约并继续审计。

**Phase 1 Gate：**

- 只有班级级路由开放；学生详情、单题详情和过程路由 reverse 失败或不可达；
- 当前成员、未参与、未分配、超时、历史参与者提示和冲突隔离正确；
- 普通 UI 不出现 Version/Session/Attempt；
- 1440×900、390×844、200% 缩放和全键盘路径通过；
- PostgreSQL 标准读负载常规页 P95≤2.0 秒；
- 零非预期 5xx、权限泄露和研究不变量回归。

## Phase 2：教师向个体和题目深钻

### 新增路由

```text
/<course_pk>/classes/<class_pk>/students/<student_pk>/
/<course_pk>/classes/<class_pk>/students/<student_pk>/experiments/<experiment_pk>/
/<course_pk>/classes/<class_pk>/experiments/<experiment_pk>/questions/<snapshot_pk>/
/<course_pk>/classes/<class_pk>/students/<student_pk>/experiments/<experiment_pk>/assignments/<assignment_pk>/process/
```

### Task 2.1：单学生详情

**Files:**

- Modify: `apps/analytics/teaching/metrics.py`
- Modify: `apps/analytics/teaching/kc_time.py`
- Modify: `apps/accounts/class_center_views.py`
- Create: `templates/teacher/class_center/student_detail.html`
- Create: `templates/teacher/class_center/student_experiment.html`
- Create: `apps/accounts/test_class_student_detail_views.py`

- [ ] 当前 roster 学生可从列表下钻；历史参与者只能从相关单实验历史区进入只读详情。
- [ ] 展示概览、实验表现、KC、本人 vs 班级事实比较和逐题记录。
- [ ] 趋势至少两个有效实验才画双折线，否则用表格/列表。
- [ ] 不生成课程综合排名。

### Task 2.2：单题详情

**Files:**

- Modify: `apps/analytics/teaching/metrics.py`
- Modify: `apps/analytics/teaching/comparisons.py`
- Create: `templates/teacher/class_center/question_detail.html`
- Create: `apps/analytics/test_question_detail_metrics.py`

- [ ] 展示题目正确率、有效作答数、distinct student 数、时长和修改过答案人数。
- [ ] 单选/判断显示选项分布和首答×终答四分类，并显示可用链覆盖率。
- [ ] 多选不强行套用四分类；数值/填空/简答不显示统一修改次数。
- [ ] 可进入某学生该题过程，但不得在班级表中批量加载原始事件。

### Task 2.3：语义化过程重放

**Files:**

- Create: `apps/analytics/teaching/process.py`
- Create: `apps/analytics/test_process_derivation.py`
- Create: `templates/teacher/class_center/process_replay.html`

- [ ] 选择题测试首答、修改、多选删除/重选、restore/autosave 排除。
- [ ] 数值/填空测试 `12→120→12.5` 快照值链，不产生统一修改次数。
- [ ] 长文本测试只返回编辑操作、字数变化、最终答案，不返回中间正文。
- [ ] 缺失/跨 Attempt/哈希不一致快照返回“不完整链”，不猜测。
- [ ] 模板上下文与 HTML 不包含 UUID、hash、raw payload、client instance ID。

### Task 2.4：旧 Session 详情兼容准备

**Files:**

- Modify: `apps/experiments/course_views.py`
- Modify: `apps/experiments/test_session_reports.py`

- [ ] 旗标打开时旧 Session 详情 302 到同班同学生单实验详情；旗标关闭保持旧行为直到 Phase 4。
- [ ] 既有 Word 报告作为受审计的高级/导出能力保留，不混入普通分析页。

**Phase 2 Gate：**

- 三类过程规则、缺链覆盖、题型适用性和时长质量测试通过；
- 过程重放 P95≤3.0 秒；
- 普通教师页零技术字段和中间长文本泄露；
- 学生作答、草稿、提交、幂等、计时、反馈全量回归通过。

## Phase 3：策略 UI 与学生“我的学习”

### 新增教师路由

```text
/<course_pk>/classes/<class_pk>/student-visibility/
```

### 新增学生路由

```text
/courses/<course_id>/
/courses/<course_id>/learning/
/courses/<course_id>/learning/analysis/
/courses/<course_id>/learning/results/
/courses/<course_id>/learning/experiments/<experiment_id>/
/courses/<course_id>/learning/experiments/<experiment_id>/questions/<assignment_id>/
```

### Task 3.1：教师设置 UI

**Files:**

- Create: `apps/accounts/class_center_forms.py`
- Modify: `apps/accounts/class_center_views.py`
- Modify: `apps/accounts/course_urls.py`
- Create: `templates/teacher/class_center/student_visibility.html`
- Create: `apps/accounts/test_student_visibility_views.py`

- [ ] 右上角增加“学生端显示设置”。
- [ ] 分成绩信息、比较信息、学习分析三组，不增加完整榜单/模型/过程开关。
- [ ] GET 显示依赖说明；POST 服务端规范化并显示 before/after 审计结果。
- [ ] 归档课程显示只读；错误角色 403，错误课程/班级 404。

### Task 3.2：学生 allow-list 投影

**Files:**

- Create: `apps/analytics/teaching/student_projection.py`
- Create: `apps/analytics/test_student_projection.py`

- [ ] 为 none/correctness/full × 七项策略 × 数据可用性 × N<5 建立参数化矩阵。
- [ ] 从允许字段正向构造 `StudentLearningDTO`；禁止教师 DTO 先构造再删字段。
- [ ] 响应对象序列化测试断言禁止字段根本不存在。
- [ ] 学生班级平均使用 macro-average；用 A/B 夹具断言 75%，且与教师 54.545% 不同。

### Task 3.3：学生视图与权限

**Files:**

- Create: `apps/assessment/learning_views.py`
- Modify: `apps/assessment/urls.py`
- Create: `apps/assessment/test_student_learning_views.py`

- [ ] 保留实验任务为 `/courses/<course_id>/` 默认页。
- [ ] 仅 active CourseEnrollment 可进入；归档课程允许只读历史。
- [ ] 跨课程、跨班、跨学生对象 404；本人但被策略禁止的详情 403。
- [ ] 班级平均和排名分别形成独立 sample/ranked 集合。

### Task 3.4：学生模板

**Files:**

- Modify: `templates/student/course.html`
- Create: `templates/student/learning/base.html`
- Create: `templates/student/learning/overview.html`
- Create: `templates/student/learning/analysis.html`
- Create: `templates/student/learning/results.html`
- Create: `templates/student/learning/experiment.html`
- Create: `templates/student/learning/question.html`
- Create: `templates/403_learning_visibility.html`

- [ ] 增加“实验任务 / 我的学习”二级导航，实验任务默认选中。
- [ ] 隐藏卡片完全不渲染，不显示“—”。
- [ ] 班级位置只在单实验详情显示，例如“7 / 38 名可排名学生；该任务应参与 45 人”。
- [ ] 班级平均显示“比较学生 24 人”，不写笼统 N。
- [ ] KC 显示本人作答计数；允许比较时另列宏平均及比较学生数。
- [ ] 学生题目页永不返回过程轨迹；full 才显示标准答案和解析。

**Phase 3 Gate：**

- 权限矩阵与响应体泄露测试全部通过；
- 学生跨身份直接 URL 测试通过；
- 移动端实验作答主路径不受分析页脚本和布局干扰；
- 100 名学生持续写入 + 3 名教师读取时达到 SLO，零事件丢失和重复提交；
- `STUDENT_LEARNING_ANALYTICS_ENABLED=0` 时学生入口和路由不可用。

## Phase 4：兼容收口、删除第二套统计与放量

### Task 4.1：旧 URL 只做重定向

**Files:**

- Modify: `apps/accounts/course_views.py`
- Modify: `apps/experiments/course_views.py`
- Modify: `apps/accounts/course_urls.py`
- Modify: `apps/analytics/test_data_center_ui_v2.py`
- Modify: `apps/experiments/test_session_reports.py`

规则：

- `/data/teaching/` 有可解析班级范围时 302 到班级中心，并保留实验业务筛选；否则到班级列表。
- 旧实验结果页只有一个班级任务时直接 302 到该单实验概要；多个班级任务时到班级任务选择页。
- 旧 Session 详情 302 到相应学生单实验详情。
- 重定向视图不得执行旧统计查询。

### Task 4.2：删除旧教学统计实现

**Files:**

- Delete: `templates/teacher/data_center/teaching.html`
- Delete: `apps/analytics/teaching_scope.py`
- Delete after consumer migration: `apps/analytics/teaching_data.py`
- Modify: `apps/accounts/course_views.py`
- Modify: `apps/analytics/services.py`
- Modify: `templates/teacher/data_center/_workbench_nav.html`
- Modify: `templates/teacher/data_center/overview.html`

删除 `teaching_data.py` 前，先把教学 XLSX 导出仍需的时长函数迁移到 `apps/analytics/teaching/kc_time.py` 并更新导入。数据中心保留教学成绩导出能力及现有实名确认/审计，但不保留教学统计页面。

### Task 4.3：文档与全量放量

**Files:**

- Modify: `README.md`
- Modify: `docs/data-center-workbench-v2.md`
- Create: `docs/class-center-metrics.md`
- Create: `docs/student-analytics-visibility.md`
- Create: `docs/class-task-migration.md`

- [ ] 指标手册明确 micro 与 macro 公式、观测分母和学生样本量。
- [ ] 迁移指南明确冲突不自动修复。
- [ ] 先全量开启教师旗标，再对演示课程开启学生旗标，最后全量学生放量。
- [ ] 回滚只关入口和旗标；不得删除新表或历史事实。

**Phase 4 Gate：**

- 旧 URL 只重定向且零旧统计 SQL；
- 代码库只有一个教学统计实现；
- 数据字典、教师指标手册和学生权限说明与代码一致；
- 全量回归、性能、视觉、无障碍和迁移演练通过。

---

## 10. 自动化测试矩阵

### 10.1 模型与迁移

- class×experiment 永久唯一；同实验跨班允许。
- task 的 release snapshot、class snapshot、experiment、class/course 对齐。
- task 实例更新、bulk update、删除和 admin 写入均失败。
- 历史唯一组回填；冲突组、空 source class 和跨课程异常不回填。
- 发布并发无半成品。
- 策略默认值、依赖、审计和归档只读。

### 10.2 范围与状态

- roster-left-join：没做实验的当前学生仍出现。
- 冻结参与资格不随转班/离课重写。
- 当前成员、未分配、未参与、进行中、完成、超时、作废、历史参与者分开。
- 全部/单实验切换同步影响所有 DTO。
- draft/archived/conflict 不进入普通全部范围。

### 10.3 指标

- 教师总体正确率 micro：6/11=54.545...%。
- 学生班级平均 macro：(100%+50%)/2=75%，sample_size=2。
- `numerator/denominator` 与 `sample_size` 类型和标签不混用。
- 有效零分、缺失、部分评分、pending/regrade/voided。
- 得分覆盖度、完成率、正确率分箱、排除原因。
- KC all_kcs 去重、多映射不可相加、涉及学生人数。
- Type 7 中位数/IQR 与时间质量。

### 10.4 排名

- 只在单实验范围。
- 未完成、超时、缺提交、缺评分、缺 score/max_score 全部不排名。
- 未舍入 Decimal、`1,2,2,4`、无时间/学号破同分。
- `ranked_count`、`assigned_count` 和 student sample_size 不混用。
- 学生排名需策略、feedback 和 ranked_count≥5。

### 10.5 过程

- 单选/判断首答与后续修改。
- 多选新增、删除、重选。
- restore、forced、autosave、重复状态排除。
- 数值/填空实际状态链与 HTML 转义。
- 长文本无中间正文。
- 首答×终答只用于完整单选/判断链。
- 技术字段只在高级工具。

### 10.6 权限与泄露

- owner/collaborator/admin 教师权限；外部教师 404。
- 学生只访问本人当前/历史合法课程对象。
- 403/404 边界。
- feedback × policy × availability × sample size 全矩阵。
- Student DTO、模板 context 和 HTML 均无禁止字段。
- 不依赖 CSS/JS 隐藏敏感数据。

### 10.7 研究不变量

- visit、draft、submit、lock、idempotency、timing、snapshot、feedback、CSRF、authorization、export 全量回归。
- 学生分析 GET 不写作答事件，不改变 Session/Attempt。
- 教师统计 GET 不写产品访问审计。

---

## 11. 性能、并发与查询预算

固定参考规模：约 300 学生、20 实验、每实验 40 题，按真实比例生成六类题目和过程事件。

标准负载：

```text
100 名学生持续写入
+
3 名教师混合读取班级概览/学生列表/实验概要
```

峰值负载：300 名学生同时在线，重点验证稳定性，不承诺普通页面 SLO。

目标：

- 常规教师页 P95≤2.0 秒；
- 单学生、单题和过程重放 P95≤3.0 秒；
- 功能/权限/迁移测试零非预期 5xx；
- 足量长压测失败率 <0.1%；
- 零事件丢失、零重复正式提交、零死锁、零同页分母/生成时间矛盾。

查询原则：

- 常规指标查询不加载 AssessmentEvent；
- 过程事件只按一个 Attempt 加载并按 ingest sequence 排序；
- 使用聚合、`select_related/prefetch_related` 和有限白名单排序；
- 不在模板循环中查询；
- 不加轮询、缓存或后台物化。

性能迁移必须由 PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` 证明。优先审查索引候选：participant snapshot 的 class/student、Session 的 class snapshot/status/student、AssessmentEvent 的 attempt/ingest sequence、GradeRecord 的 effective status/submission。只有查询计划显示顺序扫描或排序成为瓶颈时才创建后续加法式索引迁移，并把前后计划写入性能记录。

---

## 12. 视觉与无障碍验收

逐页实际验证：

- 1440×900；
- 390×844；
- 200% 浏览器缩放；
- 键盘完成范围切换、筛选、排序、分页、标签、设置和管理弹窗。

冻结要求：

- 焦点清晰；`aria-current`、label、表头、标题、错误摘要完整。
- 状态不只靠颜色；SVG 有标题、描述和数值列表等价内容。
- 普通页面无页面级横向溢出；移动端使用纵向卡片。
- 高级数据工具宽表可以保留局部横向滚动。
- 学生主操作触控目标至少 44px。
- 支持 `prefers-reduced-motion`；过渡 120–200ms。
- Bootstrap/CSS/JS/图表断网可用。
- 无生产 `window.alert()`、原生 `confirm()`。
- 普通文案只描述事实，不出现推断性心理/能力词。

---

## 13. 验证命令

每个任务先运行对应窄测试，阶段结束运行：

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test -v 1
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
node --check static/js/app.js
node --check static/js/assessment.js
node --check static/js/class-center.js
node --check static/js/advanced-data-tools.js
```

PostgreSQL 额外运行：

- 发布唯一约束并发测试；
- REPEATABLE READ/READ ONLY 同页一致性测试；
- 100 写 + 3 读标准负载；
- 300 学生峰值稳定性；
- 锁等待、死锁、重复提交、事件丢失和前后指标一致性检查。

每轮性能报告记录：数据规模、SQL 数、P50/P95/P99、错误率、锁等待、数据库 CPU/内存/IO、EXPLAIN 计划和 `generated_at` 一致性。

---

## 14. 放量停止条件

出现任一情况立即停止本期放量：

- Student DTO、模板上下文或 HTML 含被策略/feedback 禁止的字段；
- 同一班级与实验产生第二个正式任务；
- 教师总体正确率被实现成学生级宏平均，或学生班级平均被实现成作答级微平均；
- denominator 被标成学生人数，或 sample_size 被填成作答数；
- 同页指标的范围、分母或 generated_at 相互矛盾；
- 原始事件丢失、正式提交重复、评分有效性破坏或死锁；
- 历史冲突被静默合并或进入普通统计；
- 通过隐藏 UI 而不是后端授权阻止学生访问；
- 数值/填空重放跨 Attempt 解析快照，或长文本泄露中间正文；
- 旧统计实现仍被普通入口调用。

---

## 15. 明确假设

- 普通分析默认只纳入 published/closed 且无冲突的班级任务。
- 停用班级可读历史分析；归档课程所有设置和管理写操作只读。
- 当前六类题继续使用现有评分服务；本项目不新增人工批阅，但正确处理 pending/regrade。
- 班级中心不增加导出按钮；需要副本时进入数据中心并沿用现有确认与审计。
- 单实验排名 cohort 使用冻结参与者，包括后来离班的历史参与者；当前班级概览人数和学生列表仍以 active roster 为准。
- 所有统计只描述观察事实，不支持因果、心理、能力或掌握状态结论。
- 完成本计划后，基础设计冻结为 v1.0；后续新增模型推断、综合权重、实时课堂或完整榜单必须另立版本，不得夹带进入本轮实施。

---

## 16. 实施交接

推荐按 Phase 0→4 顺序执行，每个 Task 遵循：

```text
写失败测试
→ 确认测试因目标行为缺失而失败
→ 最小实现
→ 窄测试通过
→ 阶段回归
→ 小提交
```

任何阶段门禁未满足，不得以“后续再补”进入下一期。尤其不允许在 Phase 1 提前创建或隐藏 Phase 2 深钻页面，也不允许在 Phase 3 前让学生端消费教师 DTO。
