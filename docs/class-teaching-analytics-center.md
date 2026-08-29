# 班级教学分析中心运行与指标手册

## 1. 产品边界

班级中心是日常教学分析的唯一入口：教师从班级概览进入学生、实验、题目和本人过程事实；学生在“我的学习”中只看本人数据及达到保护门槛的班级汇总基准。数据中心只负责科研范围、质量、导出和高级原始数据。

普通页面只陈述可观测事实，不生成“能力”“掌握”“犹豫”“粗心”等模型判断。内部 `ExperimentVersion` 在业务和架构文档中称为“不可变实验发布快照”，普通教师/学生页面不显示版本、Session、Attempt、原始 ID、hash 或 payload。

## 2. 权威事实链

```text
当前班级成员      active CourseEnrollment
发布时应参与资格  ExperimentVersionParticipantSnapshot
班级教学任务      ClassExperimentTask
实验状态          ExperimentSession
实际题目          StudentQuestionAssignment
正式提交          SubmissionEvent
当前有效评分      completed + effective GradeRecord
题目与 KC         QuestionSnapshot
教师过程重放      AssessmentEvent / ResponseSnapshot / ResponseInputObservation
```

学生列表以当前 active roster 为全集，不能从 Session、Attempt 或评分反推成员。单实验历史参与者来自发布时冻结资格；离班学生只从该实验历史入口出现，不重新进入当前班级列表。

## 3. 指标契约

### 3.1 教师总体正确率

```text
count(effective completed GradeRecord where is_correct = true)
/
count(effective completed GradeRecord where is_correct is not null)
```

这是作答级 micro-average，名称固定为“总体正确率”。主观题待评分不进入分母，0 是有效结果，只有分母为 0 才是无数据。

### 3.2 学生个人正确率与班级平均

个人正确率使用本人同范围内的正确记录数 / 可评记录数。班级平均采用学生级 macro-average：先计算每名可比较学生的个人正确率，再等权平均；不会复用教师总体正确率。

班级平均包含本人。只有有效比较学生不少于 5 人才返回班级值。页面显示 `sample_size` 为比较学生人数；指标自身的 `numerator/denominator` 仍是观测计数。

### 3.3 得分率与评分覆盖

```text
sum(score) / sum(max_score)
```

只纳入 score 与 max_score 成对非空的当前有效完成评分。若正式提交题数大于已评分题数，学生页同时显示“已评分 x / y 题 · 部分结果”；不得把部分评分伪装成完整总成绩。单实验本人排名只纳入已完成、全部正式提交、全部评分且总满分大于 0 的冻结参与者。

### 3.4 完成、参与、KC 与时间

- 参与：存在未作废的正式实验 Session。
- 完成：Session 状态为 completed。
- KC 正确率：每个去重 KC 上的正确记录数 / 可评记录数；多 KC 题可分别贡献，KC 分母不能相加成总体分母。
- 教师用时：优先使用有效活跃区间；否则明确显示总历时估计或不可用。聚合使用中位数、IQR 和质量构成，不把估计值写成精确时长。
- 排名：只在单实验中按未舍入 Decimal 得分率计算竞赛排名 `1,2,2,4`；不生成全课程综合排名。

## 4. 学生可见性

学生最终字段是以下条件的交集：

```text
班级策略 ∩ 发布快照 feedback_mode ∩ 数据可用性 ∩ 最小样本量
```

- `none`：实验在首页仅贡献参与/完成状态，不贡献任何结果性聚合；结果详情返回 403。
- `correctness`：可按策略显示本人成绩、正确率、逐题正误和本人最终答案；不显示标准答案或解析。
- `full`：在上述基础上允许标准答案和解析。
- 策略关闭的字段不进入 Student allow-list DTO、模板上下文或 HTML，不渲染为“—”。
- 班级均值与本人位次各自计算有效集合；样本不足 5 人时字段整体不存在。
- 学生跨课程、跨班或访问他人对象返回 404；对象属于本人但策略/反馈禁止时返回带安全返回链接的 403。
- 学生端从不返回其他学生的姓名、学号、逐题答案、过程轨迹或完整排行榜。

关闭正确率会同步关闭 KC 和趋势；关闭分数会同步关闭排名。重新打开上游开关不会自动恢复过去的下游选择。课程归档后策略只读，历史可见结果仍按冻结反馈与最后策略投影。

## 5. 历史迁移与冲突

升级先执行迁移，再运行：

```powershell
python manage.py audit_class_experiment_tasks
python manage.py audit_class_experiment_tasks --format json
```

回填只接受来源班级明确、班级与实验同课程、同班同实验只有一个发布快照且匹配班级快照唯一的历史。多版本、来源为空、跨课程或重复快照都保留原始记录、报告原因码并从普通统计隔离。审计命令只读，不含学生姓名、学号、答案或原始事件，不会为了提高覆盖率自动修复历史。

数据库迁移保持加法式。回滚应用时关闭旗标，不删除 `ClassExperimentTask`、策略、发布/班级快照或历史数据。

## 6. 放量顺序与停止条件

```text
1. CLASS_CENTER_ENABLED=1
2. 在演示课程启用 STUDENT_LEARNING_ANALYTICS_ENABLED=1
3. 权限、视觉和负载验收后全量启用学生入口
```

旗标关闭时保留旧 roster 和旧教学页；教师旗标开启后，旧教学统计 URL 只重定向到班级中心。旧实验结果页按永久班级任务直接跳转或显示班级选择器。

出现以下任一情况立即关闭相应旗标：同班同实验出现第二任务；一次页面读取中分母或生成时间互相矛盾；学生 DTO/HTML 泄露禁止字段；原始事件、正式提交或评分有效性受损；历史冲突被静默合并；权限只靠隐藏 UI；PostgreSQL 出现非预期 5xx、死锁或持续超出性能预算。

## 7. 验证清单

```powershell
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.accounts apps.experiments apps.assessment apps.analytics -v 1
python manage.py collectstatic --noinput
python manage.py audit_class_experiment_tasks
python manage.py audit_class_experiment_tasks --format json
```

PostgreSQL 另行验证发布竞争、`REPEATABLE READ READ ONLY`、100 名学生写入与 3 名教师读取，以及约 300 名学生参考数据上的教师常规页 P95 ≤ 2 秒。视觉验收至少覆盖 1440×900、390×844、200% 缩放、键盘路径、无页面级横向溢出和本地资源离线可用。
