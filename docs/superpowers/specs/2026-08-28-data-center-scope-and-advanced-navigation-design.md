# 数据中心范围收缩与高级导航设计

## 状态

方案 C 已批准并冻结。普通数据中心不得重新暴露科研审计筛选；高级工具不得重新退化为 CSV 平铺目录。

## 普通教学范围

普通数据中心只显示两个范围控件：

```text
实验 + 班级
```

学生不是普通 `DataScope` 筛选条件，而是选定班级下的结果对象。版本、Session ID、Condition、Session Status、Attempt Status、Started From 和 Started Before 仍保留在底层 `DataScope`，只在高级科研筛选中使用。

界面隐藏版本不改变内部可复现机制。Session、Attempt、Submission、Grade、Condition 和发布快照继续保留原有权威语义。

## 教学 roster 不变量

教学数据列表必须以课程班级 roster 为全集，通过左连接关联 Session、Attempt、Submission 和当前 effective GradeRecord。不能从已有 Session 或 Attempt 反向生成学生集合。

因此，选定班级有 N 名有效学生时，列表必须显示 N 名学生。没有 Session 的学生显示“未参与”，其完成题数、评分、正确率和得分显示为不可用；不能从结果中消失。

数据状态至少区分：未参与、进行中、待评分、已完成和数据异常。学生列表提供姓名/学号搜索；点击学生后进入其 Session，再进入 Attempt 时间线。

## TeachingScope 与 ResearchScope

`TeachingScope` 由课程路由、`experiment_id` 和 `class_id` 组成。它以实时课程班级 ID 定位 roster，并通过冻结的 `ExperimentClassSnapshot.source_class_group_id` 关联该实验的历史 Session。

`ResearchScope` 沿用完整 `DataScope`，继续支持版本、班级快照、学生、Session、Condition、状态和时间范围。普通范围选择不得删除或重写这些科研字段；研究级 URL、导出与质量服务继续解析它们。

若同一实验/班级关联多个内部发布快照，普通界面不显示 Version 文案；高级工具显示技术告警并允许使用完整研究范围核验。

## 高级数据工具信息架构

高级工具先按研究语义分六组，再显示具体数据表：

1. 实验与参与者
2. 题目与知识结构
3. 作答与评分
4. 行为过程
5. 扩展观测
6. 派生研究数据

采用纵向折叠导航，只展开当前表所属类别。左栏只显示中文名称和逻辑表名，不显示 CSV 文件夹路径或 `.csv` 后缀。完整导出路径、schema 版本、数据层级和来源说明放在右侧“技术信息”。

`assessment_events` 必须标记为“权威总事件表 / Source of Truth”；response、navigation、exposure、telemetry 等表标记为“分类视图”。Q-matrix 属于派生研究数据，不属于题目权威映射。

Confidence 与 Simulation 入口按当前实验的发布配置及实际数据能力条件显示。未启用的模块不显示空入口。

## 交互与可访问性

范围完整时编辑器默认收起；尚未选择实验和班级时自动展开。摘要只显示实验、班级和“更改范围”。高级分类折叠项使用语义化 `<details>` / `<summary>`，当前类别自动展开；键盘焦点、当前表状态和折叠状态必须可识别。

高级工具提供表搜索，按中文名称、逻辑表名和描述过滤；搜索只改变左栏可见项，不改变当前数据范围。

## 非目标

- 不删除或改写现有科研数据模型。
- 不改变 ZIP 内的文件目录结构。
- 不把 Q-matrix 提升为原始权威数据。
- 不在普通页面重新加入学生、版本、Session、Condition、状态或时间筛选。

## 验收标准

- 普通范围表单只包含实验和班级字段。
- 班级内无 Session 的学生仍出现在教学列表并标记“未参与”。
- 普通页面不出现 Version、Session ID、Condition、状态或 UTC 时间输入。
- 高级工具只展开当前类别，左栏不显示完整 CSV 路径。
- 高级页显示六个语义分组、技术信息和权威/分类视图标记。
- 未启用的 Confidence/Simulation 入口不可见。
- 既有 `DataScope`、导出、质量、权限和科研事件契约测试保持通过。
