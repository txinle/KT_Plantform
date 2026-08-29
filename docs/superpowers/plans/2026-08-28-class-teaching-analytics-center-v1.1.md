# 班级教学分析中心与“我的学习”实施计划 v1.1

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将班级详情从名单管理页升级为班级级教学分析中心，并在教师策略、实验反馈和样本量共同约束下，为学生提供只包含本人数据与受保护班级基准的“我的学习”。

**Architecture:** Django 单体保持现有不可变发布事实链；新增永久 `ClassExperimentTask` 将“班级 × 实验”固定为可审计的教学任务。教师和学生共用只读统计原语，但学生 DTO 必须由 allow-list 正向构造；常规页在一次一致性读取中按需聚合，过程原始数据只在教师深钻页加载。

**Tech Stack:** Python、Django 5.2、PostgreSQL（生产与并发验证）、SQLite（功能开发）、Django Templates、Bootstrap 5.3、本地 CSS、原生 JavaScript、原生 SVG。

---

## 1. 本版冻结结论

### 1.1 已解决的五个原始规格歧义

1. **分期不重叠。** Phase 1 只有班级级概览、学生列表、实验列表/单实验概要和班级管理；学生详情、单题详情、选项分布和过程重放全部属于 Phase 2。
2. **教师指标改名。** 教师页的 response-level pooled accuracy 固定叫“总体正确率”，绝不叫“平均正确率”。
3. **学生比较单独定义。** “班级平均正确率”是先计算每个可比较学生的个人正确率，再做等权算术平均（macro-average）。
4. **计数语义分离。** `numerator/denominator` 是公式观测数；`sample_size` 仅是进入比较统计的独立学生人数。
5. **过程和术语明确。** 数值/短填空可显示实际状态值链但不生成跨题型“修改次数”；普通产品与架构文案使用“不可变实验发布快照”，内部仍映射 `ExperimentVersion`。

### 1.2 本版新增的实现级不变量

1. `ClassExperimentTask` 建立后，`task.class_group.course_id == task.experiment.course_id` 是**持续**不变量：任何 ORM 实例保存、default manager、base manager 或 Admin 均不得再改写 `ClassGroup.course` 或 `Experiment.course`。班级 `is_active` 等非课程字段仍可变。
2. 任务、任务引用的发布快照和班级快照不可变；课程归档、班级停用、实验关闭/归档都不释放永久业务键 `class_group × experiment`。
3. 任务创建只能经 `publish_experiment()` 的受信任服务路径，且必须同时处于发布私有作用域和 `transaction.atomic()`。该规则防止普通业务 API、管理页、未加事务的代码和 QuerySet 批量写入；不把 Python 同进程私有名称当作抵抗恶意代码或 raw SQL 的安全边界。
4. 历史歧义不推断、不修复、不合并：只报告并从普通统计隔离。

### 1.3 发布零参与者的待确认决策（阻断行为变更）

当前代码及既有回归规定：最终 roster 为零时发布失败。当前任务实现已经满足较窄且兼容的语义：**只要同一次发布的最终 roster 至少有一名学生，每个已选的空班或全排除班也会得到零参与者班级快照和永久任务。**

“唯一已选空班/唯一全排除班也允许发布”的含义会推翻现有发布生命周期，不能在没有产品授权时暗改。实施开始前必须在下列两项中选一项：

- **A（兼容既有行为）：** 全局 roster 为零仍拒绝发布；计划中所有“空班”要求均以上述至少一名总体参与者为前提。
- **B（扩展任务语义）：** 有至少一个已选课程班级即可发布，即使最终 roster 为零；必须修改 `validate_for_publish()`、替换现有拒绝测试、审查学生入口/完成率/评分/导出对零参与者版本的假设，并在真实库副本验证。

在用户明确选择前，代码按 A 保持不变。

### 1.4 v1 不做的事情

- 全课程综合排名、完整实名排行榜、综合能力分；
- KT/CD 普通展示，或“掌握、犹豫、粗心、能力”等推断性文案；
- 实时课堂监控、轮询、通知、延迟公布模式、人工评分新工作流、班级页导出；
- 物化统计表、后台聚合/缓存失效体系；
- React/Vue/HTMX、npm、远程字体、额外图表库。

## 2. 权威事实链、范围和可见性

```text
当前班级 roster     active CourseEnrollment
应参与资格          ExperimentVersionParticipantSnapshot
班级任务            ClassExperimentTask
实验状态            ExperimentSession
题目分配            StudentQuestionAssignment
正式提交            SubmissionEvent
当前有效评分        effective GradeRecord
题目/KC             QuestionSnapshot + 去重 all_kcs
过程状态            AssessmentEvent + ResponseSnapshot + ResponseInputObservation
```

- roster 永远是学生列表全集；禁止从 Session、Attempt、提交或评分反推“班级成员”。
- 普通班级统计仅纳入任务、发布快照、班级快照完全一致且实验为 `published` 或 `closed` 的任务；草稿、归档、冲突任务不参与“全部实验”。
- 教师可在授权班级内看个体比较；学生只能看本人，及达到样本门槛的汇总基准，永不看他人身份、逐题记录或过程。
- 学生最终可见字段必须等于：`班级策略 ∩ Experiment.feedback_mode ∩ 数据可用条件 ∩ 样本量规则`；先构造 `StudentLearningDTO`，不得从教师 DTO 删除字段。

## 3. 统计契约

| 使用场景 | 名称 | 公式 | 样本含义 |
| --- | --- | --- | --- |
| 教师班级概览/单实验 | 总体正确率 | `count(effective completed GradeRecord is_correct=True) / count(is_correct 非空)` | 分母为有效已评分作答，非学生数 |
| 学生个人 | 我的正确率 | 本人同范围正确记录 / 本人同范围已评分记录 | 不适用 |
| 学生与班级比较 | 班级平均正确率 | `mean(每名可比较学生的个人正确率)` | `sample_size=进入均值的 distinct student` |
| KC | 知识点正确率 | 该 KC 关联题目上的正确记录 / 已评分记录 | 多 KC 可各自贡献，但不得相加为总体 |
| 教师得分 | 已评分得分率 | `sum(score) / sum(max_score)`，仅成对非空有效评分 | 分母为分值，不是人数 |

使用夹具固定口径：A 为 `1/1`、B 为 `5/10` 时，教师总体正确率必须为 `6/11=54.545…%`；学生比较的班级平均必须为 `75%`、`sample_size=2`。`0` 是有效结果；只有分母为零、冲突隔离或无权限才是无数据。

## 4. 目标文件图

| 文件/目录 | 责任 |
| --- | --- |
| `apps/experiments/models.py` | 任务、发布/班级快照不变性与课程归属守卫 |
| `apps/experiments/services.py` | 原子发布、历史回填共用的任务创建服务 |
| `apps/experiments/migrations/0011_*`—后续迁移 | 加法式任务表、历史回填和模型选项 |
| `apps/experiments/management/commands/audit_class_experiment_tasks.py` | 只读冲突审计（文本/JSON） |
| `apps/accounts/models.py` | `ClassStudentAnalyticsPolicy` 与班级课程归属守卫 |
| `apps/accounts/student_analytics_policy.py` | 策略保存、规范化、行锁、授权与审计 |
| `apps/analytics/teaching/` | 不可变 DTO、范围、指标、比较、KC/时间、过程与学生投影 |
| `apps/analytics/class_views.py`（或现有课程/班级视图模块） | 教师班级页面、服务端过滤/排序/分页 |
| `apps/analytics/student_views.py`（或现有课程视图模块） | 学生“我的学习”只读页面 |
| `templates/`、`static/` | Bootstrap 本地模板、原生 SVG、可访问响应式布局 |
| `config/settings.py` | `CLASS_CENTER_ENABLED`、`STUDENT_LEARNING_ANALYTICS_ENABLED` |
| `docs/` | 指标手册、迁移/冲突指南、学生可见性与数据中心职责 |

## 5. 实施任务

### Task 0：迁移前置检查与可重复基线

**Files:** 只读 `manage.py`、迁移目录、既有教学/快照/评分测试；创建本计划相关测试夹具。

- [ ] 复制真实生产库的脱敏副本；在副本上记录：

  ```powershell
  python manage.py showmigrations accounts experiments assessment analytics
  python manage.py check
  python manage.py makemigrations --check --dry-run
  ```

  期望：无未解释的迁移缺口；若有缺口，先按既有迁移顺序升级副本，绝不在缺口库直接加入新迁移。

- [ ] 运行基线契约：

  ```powershell
  python manage.py test apps.analytics.test_teaching_data apps.analytics.test_data_center_ui_v2 apps.experiments.test_class_snapshots apps.assessment.test_submission_grade_contract -v 2
  ```

  期望：全部通过；保存总数与耗时，作为后续差异基线。

- [ ] 在干净 SQLite 测试库和 PostgreSQL 副本各跑一次 `migrate --noinput`；记录 `showmigrations` 输出。任何冲突迁移或不可逆数据丢失立即停止 Phase 0。

### Task 1：班级实验任务、发布原子性和持续课程对齐

**Files:**
- Modify: `apps/experiments/models.py`
- Modify: `apps/experiments/services.py`
- Modify: `apps/experiments/admin.py`
- Modify: `apps/accounts/models.py`, `apps/accounts/admin.py`
- Create/Modify: `apps/experiments/migrations/0011_class_experiment_task.py` 及其后续加法迁移
- Test: `apps/experiments/test_class_experiment_tasks.py`

- [ ] 写失败测试，覆盖同班同实验二次创建失败、同实验跨班成功、班级/实验/版本/快照/发布时间不一致失败；测试任务实例、`objects`、`_base_manager` 的 update/bulk_update/delete/bulk_create 都失败。

  ```python
  with self.assertRaises(ValidationError):
      ClassExperimentTask._base_manager.filter(pk=task.pk).update(
          published_at=task.published_at
      )
  ```

- [ ] 实现 `ClassExperimentTask`：`class_group`、`experiment`、`release_snapshot`、`class_snapshot`、`published_at`、`created_at`；设置 `PROTECT`、`OneToOneField`、`unique_class_experiment_task`、`class_task_class_pub_idx`。`clean()` 同时比较课程、版本、快照来源和发布时间。

- [ ] 对 task-backed `ExperimentVersion` 和 `ExperimentClassSnapshot` 阻止实例/QuerySet/base-manager 的更新和删除；Admin 禁止任务增改删，task-backed Version 只读。

- [ ] 将 `publish_experiment()` 保持为单一 `@transaction.atomic`：锁实验和参与规则，创建版本、题目/班级/参与者快照，为每个已选班级创建任务，再更新实验状态。当最终 roster 至少一人时，已选但空 roster 或全排除班级也必须有零参与者快照和任务。

- [ ] 在执行发布零参与者行为前，先取得第 1.3 节的 A/B 决策。选择 A 时保留现有“最终 roster 至少一人”的校验并覆盖混合空/非空班；选择 B 时才将 `validate_for_publish()` 扩展为“已选班级即可发布”，替换旧 empty-final-roster 测试，并为学生入口、完成率、评分和导出补齐零参与者审计。

- [ ] 任务保存必须检查发布私有作用域和原子事务：

  ```python
  if not (_class_experiment_task_creation_is_active() and connection.in_atomic_block):
      raise ValidationError("班级实验任务只能在实验发布事务中创建。")
  ```

  仅 private scope 是代码组织约定；不实现 `inspect` 调用栈检查。任何“恶意同进程代码”仍可 raw SQL，不能被 Django 模型伪装为安全边界。

- [ ] 为 `Experiment` 与 `ClassGroup` 建立只在 `course`/`course_id` 写入时触发的 QuerySet/实例守卫：查询集含关联任务即抛出 `ValidationError`；不拦截 task-backed 班级的 `is_active` 更新。Admin 把 task-backed 对象的课程字段设为只读或禁止改动，并为无 500 的表单路径写测试。

- [ ] 课程守卫必须是**锁定—复核—写入**，不是 `exists()` 后直接 `UPDATE`：对课程字段 QuerySet 写入，先设置 `_for_write=True` 解析唯一 write alias；在原 QuerySet 范围 `select_for_update()` 锁定目标 parent，再在同一事务中检查关联任务。最终更新必须在原 QuerySet clone 上追加已锁定 ID，以保留 annotations、表达式和过滤语义，并显式调用 Django 基类 `QuerySet.update` 防止递归；实例 `save()` 的真实课程变更使用同一协议。锁顺序与发布一致（先 Experiment、后 ClassGroup）。SQLite 只验证功能路径；PostgreSQL 必须以双事务验证“课程更新先获得锁时发布重验失败，发布先获得锁时课程更新拒绝”，二者均不能在任务建立后改写课程。

- [ ] 批量 API 的边界：Task、Version、ClassSnapshot 不支持 `bulk_create(update_conflicts=True)`；Experiment/ClassGroup 只要 upsert 的 `update_fields` 含课程字段即拒绝，非课程 upsert（如 `is_active`、draft description）可用。`bulk_update` 必须沿用锁定 `update()` 守卫，空 QuerySet 返回 `0`，不得以 model default manager 预检而扩大 scope。

- [ ] 运行 RED/GREEN：

  ```powershell
  python manage.py test apps.experiments.test_class_experiment_tasks -v 2
  python manage.py test apps.experiments -v 1
  python manage.py check
  python manage.py makemigrations --check --dry-run
  ```

  然后提交 `feat: add immutable class experiment tasks` 与独立的守卫修复提交。

### Task 2：历史回填与冲突隔离

**Files:**
- Create: `apps/experiments/migrations/0013_backfill_class_experiment_tasks.py`（编号按实际前序迁移递增）
- Create: `apps/experiments/management/commands/audit_class_experiment_tasks.py`
- Test: `apps/experiments/test_class_experiment_task_migration.py`
- Test: `apps/experiments/test_audit_class_experiment_tasks.py`

- [ ] 使用 `MigrationExecutor` 写四个历史夹具：唯一版本/唯一来源班级（应回填）；多版本；空 `source_class_group_id`；跨课程或重复班级快照（均不得回填）。迁移操作使用 `apps.get_model()`，不得调用运行时模型守卫。

- [ ] 回填算法只接受以下全真条件：源班级非空、班级/实验同课程、同 `(class_group, experiment)` 仅一个 distinct version、该版本仅一个匹配班级快照；否则不创建任务、不改写任何历史记录。

- [ ] 实现只读审计命令：文本和 `--format json` 都输出 course/class/experiment、版本数、快照数、参与者数、Session/Attempt/GradeRecord 数和原因代码；JSON 测试断言不含学生姓名、学号、答案、原始事件或正文。

- [ ] 在真实库副本运行迁移和命令两次；第二次必须零写入且输出稳定。记录冲突数量，不把冲突数据“修干净”以换取统计可用。

### Task 3：学生可见性策略与特性旗标

**Files:**
- Modify: `apps/accounts/models.py`, `apps/accounts/admin.py`, `config/settings.py`
- Create: `apps/accounts/student_analytics_policy.py`
- Create: `apps/accounts/migrations/0008_class_student_analytics_policy.py`（实际编号按依赖递增）
- Test: `apps/accounts/test_student_analytics_policy.py`

- [ ] 添加每班一条 `ClassStudentAnalyticsPolicy`：`show_scores`、`show_correct_rate`、`show_item_results`、`show_class_average`、`rank_mode(none|self)`、`show_kc_rates`、`show_trend`、`updated_by`、`updated_at`。不要添加“完整排行榜”“KT/CD”“学生过程”开关。

- [ ] 写失败测试：关闭 `show_correct_rate` 后服务保存结果必须关闭 KC/趋势；关闭分数后必须 `rank_mode=none`；重新打开上游开关不得恢复旧下游值；课程归档拒绝写；owner/collaborator/admin 可写、越权拒绝。

- [ ] 实现 `save_class_student_analytics_policy()`：用 `transaction.atomic()` 与 `select_for_update()` 锁策略/班级，显式规范化依赖，记录 only before/after policy 字段的审计详情，不读写学生明细；普通 GET 不产生访问审计。

- [ ] 增加默认关闭旗标：

  ```python
  CLASS_CENTER_ENABLED = os.getenv("CLASS_CENTER_ENABLED", "0") == "1"
  STUDENT_LEARNING_ANALYTICS_ENABLED = (
      CLASS_CENTER_ENABLED
      and os.getenv("STUDENT_LEARNING_ANALYTICS_ENABLED", "0") == "1"
  )
  ```

- [ ] 对已有班级回填默认策略；Phase 0 不创建设置页面和学生读路由。提交 `feat: add student analytics policy and feature flags`。

### Task 4：统计内核、DTO 与一致读取

**Files:**
- Create: `apps/analytics/teaching/{__init__,contracts,scope,metrics,comparisons,kc_time,process,student_projection}.py`
- Test: `apps/analytics/test_teaching_contracts.py`, `apps/analytics/test_teaching_metrics.py`

- [ ] 定义不可变 `MetricValue`、`StudentComparison`、`DataState`、`ExclusionReason`、`ClassAnalysisScope`。每个 DTO 需要同一 `generated_at`；模板不得自行除法、四舍五入或推断分母。

- [ ] 实现仅包统计查询的 `analytics_read_transaction()`：PostgreSQL 使用 `REPEATABLE READ READ ONLY`，SQLite 保持兼容；在事务内一次生成 `generated_at`，结束后才渲染。

- [ ] 对“全部实验/单实验”范围写夹具：范围切换必须同时改变四卡、分布、状态、KC、学生行和实验行。roster、冻结参与资格、Session、提交、有效评分各自独立查询再连接，禁止用缺失 Session 排除 roster 成员。

- [ ] 为 A=`1/1`、B=`5/10` 写双口径测试；为 pending、voided、`is_correct=None`、有效零分、重评分、未提交、超时与冲突写排除原因测试。

### Task 5：教师班级级服务（Phase 1）

**Files:**
- Modify: `apps/analytics/teaching/metrics.py`, `comparisons.py`, `kc_time.py`
- Test: `apps/analytics/test_class_center_metrics.py`

- [ ] 实现且只实现班级级事实：当前班级人数、参与人数、总体正确率、实验完成率、正确率分布（含“无数据”）、三态参与情况、低到高 KC 正确率、最近实验表、当前 roster 学生汇总表、单实验题目摘要。

- [ ] 学生表必须保留未参与者；支持服务端白名单排序、搜索、状态过滤、分页。全部范围永不产生排名；单实验排名只在完整评分资格、未舍入 Decimal 排序后计算并采用竞赛排名 `1,2,2,4`。

- [ ] 题目摘要只显示题目、KC、作答人数、总体正确率、正确/错误和平均用时；不链接单题详情，不加载选项分布、个体名单或过程。

- [ ] 加索引前先用 PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` 固定查询预算；只新增能被计划证实使用的复合索引。

### Task 6：教师班级中心路由、权限与界面（Phase 1）

**Files:**
- Modify: 现有班级/课程 URL 与 view 模块、`templates/`、`static/`
- Test: `apps/analytics/test_class_center_views.py`, `apps/analytics/test_class_center_ui.py`

- [ ] `CLASS_CENTER_ENABLED=0` 保持旧 roster 页；开启后班级入口为：概览、学生表现、实验分析、班级管理。课程数据中心不复制日常统计。

- [ ] owner/collaborator/admin 可读；外部教师和错误课程/班级组合返回 404；所有写操作复用既有 URL/form/CSRF/审计语义。

- [ ] 桌面使用语义表格，移动端将学生行折叠为卡片；概览四指标、100% 状态条和原生 SVG KC 水平条必须有文本等价内容。不得引入前端统计计算、远程资源、`alert()` 或 `confirm()`。

- [ ] 显示统一“统计生成时间”；进行中实验提示结果可能变化。普通页面不得出现 Version、Session、Attempt、snapshot UUID、raw payload 或技术错误文本。

- [ ] 验收 1440×900、390×844、200% 缩放、键盘 Tab 路径、无脚本降级与静态文件收集。

### Task 7：教师深钻服务（Phase 2）

**Files:**
- Modify: `apps/analytics/teaching/{metrics,comparisons,kc_time,process}.py`
- Test: `apps/analytics/test_teacher_drilldown.py`, `apps/analytics/test_process_projection.py`

- [ ] 实现当前 roster 学生详情：概览、实验表现、KC 本人 vs 班级宏平均、逐题事实；历史参与者只能从其单实验历史入口只读进入。

- [ ] 实现单题详情：选择/判断题显示选项分布和首答×终答四分类；多选不强套四分类；所有题型报告链可用覆盖率。

- [ ] 过程服务按题型投影：选择题显示值链；数值/短填空显示例如 `12 → 120 → 12.5 → 提交` 而不出跨题型修改次数；长文本只显示操作类别、字数变化与最终答案，绝不返回中间正文。链缺失、跨 Attempt、哈希不一致时返回“不完整链”，不猜测。

- [ ] 深钻页按需分页读取过程，永不在班级表预加载原始事件；模板与 DTO 测试断言不包含 UUID、hash、client ID、原始 payload 或中间长文本。

### Task 8：教师深钻路由和页面（Phase 2）

**Files:** 现有 URL/view 模块、教师模板/CSS/JS；测试相应 view/UI。

- [ ] 增加经过班级授权的路由：学生详情、学生的单实验详情、单题详情、教师过程回放；一切跨课程/班级/学生/题目组合返回 404。
- [ ] Phase 1 的学生姓名/学号在 Phase 2 前没有链接；开放后只链接当前 roster，历史路径从单实验页面进入。
- [ ] 学生趋势至少两个有效实验才画两线 SVG，否则只用列表；不产生课程综合排名或推断标签。

### Task 9：学生端策略设置、allow-list DTO 和权限矩阵（Phase 3）

**Files:**
- Modify: `apps/accounts/student_analytics_policy.py`, 教师班级设置 view/template
- Modify: `apps/analytics/teaching/student_projection.py`
- Test: `apps/analytics/test_student_visibility.py`

- [ ] 仅在教师旗标启用后增加“学生端显示设置”；设置分成绩、比较、学习分析三组，显示依赖说明。课程归档为只读，GET 不写审计，POST 走 Task 3 服务。

- [ ] 参数化覆盖 `feedback_mode=none/correctness/full × policy 开关 × 数据可用 × N<5`。每个响应序列化测试都断言禁止字段**根本不存在**，而不是前端隐藏。

- [ ] 分数受 `show_scores ∩ feedback_mode` 控制；正确率/KC/趋势受正确率策略控制；班级平均样本 `<5` 不返回；排名仅 `rank_mode=self` 且仅单实验、完整可比条件满足时返回本人位置。

### Task 10：学生“我的学习”页面（Phase 3）

**Files:** 课程 URL/view、学生模板/静态文件；`apps/analytics/test_student_learning_views.py`、UI 测试。

- [ ] 保留“实验任务”为课程默认页，新增“我的学习”二级导航。仅 active `CourseEnrollment` 可进；归档课程可只读历史；本人但策略禁止的细节返回 403，跨对象返回 404。
- [ ] 概览显示本人完成、正确率、得分率，必要时显示班级平均与“比较学生 X 人”；禁止把被策略关闭的卡片渲染为 `—`。
- [ ] 实验页只显示本人逐题结果和实验许可时的标准答案/解析；绝不显示其他学生答案、姓名、排行榜、答题过程或教师聚合过程。
- [ ] 响应式、无障碍和离线资源验收同教师端；不新增前端数据接口绕开服务器投影。

### Task 11：兼容收口、文档、性能与放量（Phase 4）

**Files:** 旧统计 view/template/服务兼容层、`README.md`、`docs/`、数据中心文档、指标手册、迁移和冲突指南。

- [ ] 旧实验结果路由在旗标开启时重定向到唯一班级任务入口；旗标关闭保留旧行为直到全量验证结束。删除旧统计服务前确认新页面无调用者，禁止长期维护两套公式。
- [ ] 文档必须写出：micro/macro 公式、观测分母 vs 学生样本量、冲突不自动修复、学生可见性、数据中心仅承担科研/质量/导出/高级工具。
- [ ] 按顺序放量：全量教师旗标 → 演示课程学生旗标 → 全量学生旗标。应用回滚只关入口/旗标，不删除表、任务、快照或历史事实。

## 6. 不能跳过的最终验证

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.accounts apps.experiments apps.assessment apps.analytics -v 1
python manage.py collectstatic --noinput
python manage.py audit_class_experiment_tasks
python manage.py audit_class_experiment_tasks --format json
```

在 PostgreSQL 脱敏副本另行执行：

1. 从当前生产迁移状态升级，运行回填两次并比对审计；
2. 两个并发 `publish_experiment()` 竞争相同 `class_group × experiment`，断言一次成功、一次得到业务错误、没有半成品版本/快照/任务；
3. 以目标规模（约 300 名学生）跑常规教师 GET，P95 ≤ 2 秒、零非预期 5xx；
4. 对教师/学生页面实测桌面、移动、200% 缩放、键盘与权限矩阵。

## 7. 放量停止条件

立即停止并回退旗标，而不是删除迁移，若发生任一情形：第二个同班同实验任务；统计页同次读取的分母或生成时间互相矛盾；学生 DTO/HTML 含禁止字段；原始事件/正式提交/评分有效性受损；历史冲突被静默合并；通过隐藏 UI 而不是服务端授权阻止访问；生产查询超预算或出现非预期 5xx。

## 8. 规格覆盖自检

- Phase 1/2 边界：Task 5–6 与 Task 7–8 分离。
- micro/macro 与计数含义：第 3 节、Task 4–5、Task 9。
- 数值/填空过程和长文本隐私：Task 7。
- 内部 `ExperimentVersion` 术语降级：第 1、2 节与普通 UI 禁止项。
- 历史回填/冲突：Task 2。
- 学生权限：Task 3、9、10。
- 性能、并发、可访问性和回滚：Task 6、11 和第 6–7 节。
