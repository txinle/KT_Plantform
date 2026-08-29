# RecordingCoverage v1 工程验证报告

> 日期：2026-08-29
> 分支：`feat/class-teaching-analytics-center`
> 性质：工程验证与浏览器预演，不是对 300 秒宽限期进行经验估计的真实课堂 Pilot。

## 1. 自动化矩阵

| 计划项 | 自动化证据 |
|---|---|
| T01–T04 选择/判断/多选状态链 | v2 schema、接口所有权/选项校验、过程投影与 collector 测试 |
| T05–T07 数值/填空/长文本 | 词法状态重放、稳定批次字段、IME/隐私测试 |
| T08–T10 导航与返回 | `item_left.to_assignment_id`、Visit/导航重放测试 |
| T11–T12 visibility/focus | lifecycle 与 ActiveTime 排除规则测试 |
| T13–T15 离线、尾部和强退 | outbox 重试、tail gap/unknown、checkpoint 测试 |
| T16–T18 提交/幂等/终态 | 现有 Submission/Grade/事件事务测试与 terminal constraint |
| T19–T20 条件采集 | Confidence 现有分配与权限回归；关闭为 not applicable |
| T21–T24 gap/ACK/坏事件 | 精确缺号、连续 ACK、dead letter、批内隔离测试 |
| T25–T27 迟到/历史/后验校时 | 新 Run、legacy propagation、sync 输入 hash/派生时间 |
| T28 导出一致性 | 数据中心、UI、lineage 目标测试 |
| T29 权限与隐私 | 教师深钻、学生 allow-list、无中间正文测试 |
| T30 规模 | 300 Session、15,000 event、单 CoverageRun < 5 s |

## 2. 数据库与性能

| 环境 | 结果 |
|---|---|
| SQLite 最终全量 | 806 tests，OK，9 skipped |
| PostgreSQL 最终目标矩阵 | 105/105 |
| JavaScript collector | 21/21 |
| SQLite 300/15,000 | 通过 5 秒硬门槛 |
| PostgreSQL 300/15,000 | 通过 5 秒硬门槛 |

PostgreSQL 数据中心测试最初因 Django `TestCase` 外层事务先执行查询而无法设置导出事务隔离级别。测试已改用 `TransactionTestCase`；生产端 `REPEATABLE READ READ ONLY` 未删除、未降级。

全量回归还暴露并修复了一个 v1 兼容问题：旧客户端上传不含 v2 稳定编辑批次字段的 `text_edit_observed` 时，投影层曾错误地强制读取这些字段。当前实现接受合法 v1 事件，不伪造批次时间；只有 v2 事件才执行 v2 字段契约。

## 3. 浏览器预演

使用迁移后的本地业务库并开启教师/学生分析旗标：

- 班级入口显示概览、学生表现、实验分析、班级管理和学生端显示设置；
- 1440×900、390×844 与 720×450 CSS 视口代理下无页面级横向溢出；
- 390px 内宽表保持组件内滚动，不扩张页面；
- 题目分析可进入学生完整过程页；
- 教师过程页以中文展示“过程记录”“时间依据”和“缺失说明”；原始 Coverage 状态码、缺失原因码及 CoverageRun UUID 只在研究导出中保留；
- 页面未出现“犹豫、注意力不集中、能力下降、掌握不足”等推断词。
- 最终烟测确认课程概览存在“班级教学分析”入口，班级列表存在“进入班级中心”入口；过程页无脚本警告或错误。

720×450 CSS 视口仅是对较窄可视区域的代理检查，不能替代真实浏览器的 200% 页面缩放验收。真实 200% 缩放下的浏览器验证尚未执行，仍是发布前必须完成的 UI 验收项。

预演首次发现本地业务库未应用新增迁移，题目深钻返回 500；执行 `migrate` 后恢复。这说明部署门禁必须把迁移置于服务切换之前。

当前演示 Session 是人工构造的过程样本，仅用于验证页面投影和缺失原因展示；它不能替代真实课堂 Session，也不能用于估计新 collector 的 complete 率。

## 4. 尚未由工程验证回答的问题

以下问题必须由真实小规模课堂 Pilot 回答，不能从合成测试外推：

- 300 秒终态宽限覆盖多少迟到事件；
- 不同浏览器/设备的 IndexedDB 不可用率和 `tail_unknown` 分布；
- 真实网络下 dead letter、sequence gap、time sync quality 的发生率；
- 长文本 800 ms 批次对不同输入法的研究可用性；
- complete/partial/insufficient 在真实 Session 中的分布。

在这些结果出现前，不调整 `coverage-v1` 参数，也不把 Coverage 直接作为 KT/CD 输入。
