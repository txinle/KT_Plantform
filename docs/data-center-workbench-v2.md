# 数据中心工作台 v2

## 面向用户的页面边界

数据中心不再把 CSV 表和研究包当作一级导航。教师入口固定为：

```text
概览 → 教学数据 → 导出 → 数据质量 → 导出记录 → 高级数据工具
```

“教学数据”按学生汇总、学生会话、单题作答时间线三级钻取；只有“高级数据工具”显示原始表、JSON、schema 和血缘。这个边界防止普通教学操作依赖数据库内部结构，也防止研究数据包被误作日常成绩表。

## 普通教学范围与 ResearchScope

普通数据中心只把实验和实时课程班级作为 `TeachingScope`。学生不是默认查询参数，而是班级 roster 中的结果对象；无 Session 的学生仍进入教学列表，并明确标记为“未参与”。

教学查询以 `ClassGroup.id` 定位完整 roster，再通过 `ExperimentClassSnapshot.source_class_group_id` 关联历史 Session、Attempt、Submission 和当前 effective GradeRecord。学生姓名/学号搜索与 Session/Attempt 钻取是临时视图参数，不写入记忆范围。

完整 `DataScope` 继续保留版本、班级快照、学生、Session、Condition、状态和 UTC 时间边界，供导出、质量检查和研究级 URL 使用。所有非空过滤条件严格取交集并执行关系校验；不会为了得到结果而静默删除冲突条件。KT/CD 等派生包仍按单一发布版本生成，普通教学界面不暴露 Version 控件。

## 可信教学统计

成绩取 Submission 的当前 effective `GradeRecord`，而不是简单取最新一条评分。待评分、重评分、废弃评分与历史投影都保留独立语义。

时长由 `TimingSummary` 计算。原生心跳足够时显示活跃作答时长；数据质量降级时回退为总历时并明确标记；没有可靠起止点则显示不可用。网页和教学成绩 XLSX 使用同一服务，避免口径漂移。

## 质量与导出

质量页分别报告 identity、state chain、submission、ordering/time 和 provenance。结果使用 `PASS`、`WARN`、`FAIL`、`NOT_EVALUABLE`、`NOT_APPLICABLE` 五态；Legacy 投影无法核验原生事件时必须是不可评估，而不是失败。

导出先选择用途，再在实名范围确认页提交。每次提交创建独立 `ExportJob` 和冻结的 `ExportSnapshot`，记录截止点、事件高水位、schema 与派生血缘。Purge 删除源数据与文件，但保留去标识化的导出审计 tombstone；不能借 append-only 规则规避合法撤回。

## 高级数据工具

高级工具按“实验与参与者、题目与知识结构、作答与评分、行为过程、扩展观测、派生研究数据”六类折叠。左栏只显示中文名称与逻辑表名；物理导出路径、Schema 和来源层级只显示在右侧技术信息中。

`assessment_events` 是权威总事件表，response/navigation/exposure/telemetry 等是从该总表得到的分类视图。Q-matrix 在导航语义上属于可复现派生；为了保持已有研究包兼容性，其 ZIP 物理路径不随界面分类变化。Confidence 与 Simulation 只有在当前实验启用相应模块时才显示。
