# 班级、题库、个体化抽题与会话报告记录

- 日期：2026-08-21
- 范围：课程永久题目标识、六题型与按题型配额抽题、参与者规则发布、班级/题库元数据编辑、会话详情与 Word 报告

## 功能

- 课程题目获得不复用的 `course_sequence` 和 `stable_code`；发布快照保留课程永久编号、来源题库 ID/名称、题库内序号和原题目标识。
- 实验支持全量题池或按六种题型配额抽题。发布冻结完整可用候选池、库存、配额、抽样盐和算法版本；会话保存独立的抽题种子和排序种子。
- 草稿参与者规则支持按班级纳入、显式纳入和从已选班级排除。发布时重新核验有效选课关系并冻结班级快照。
- 新增班级和题库元数据的独立 GET 编辑页与 POST 更新端点；新增课程实验的参与规则、会话详情和 POST Word 下载端点。
- 会话详情和 DOCX 按学生实际分配顺序显示作答。DOCX 不包含 KC、难度、用时、导航或解析；配额抽题下不得直接比较跨学生裸分。

## 数据库迁移与依赖

- `question_bank.0003_course_question_identity`、`0004_enforce_positive_course_question_sequence`、`0005_alter_question_question_type`。
- `experiments.0003_snapshot_course_question_identity`、`0004_experiment_selection_mode_and_more`、`0005_experiment_participant_rules`。
- `assessment.0002_experimentsession_sampling_seeds`。
- Word 报告使用 `python-docx==1.2.0`；运行 `python -m pip install -r requirements.txt` 安装。

## 兼容性

- 已发布版本继续只读取不可变 `QuestionSnapshot`；没有抽样盐的旧版本保留 `legacy-v0` 排序流。
- 旧表单未提交选题字段时默认使用全量题池；现有实验补齐六条零配额记录。
- 匿名 KT 与匿名导航 CSV 的 URL 和 headers 不变；身份对照 CSV 保留既有的独立映射 headers；不向匿名数据 CSV 添加实名或课程永久编号字段。

## 验证命令

    python manage.py check
    python manage.py makemigrations --check --dry-run
    python manage.py test apps.question_bank.tests_course_question_identity apps.experiments.tests_course_question_identity apps.experiments.test_selection_sampling apps.experiments.test_participant_rules apps.experiments.test_session_reports
    git diff --check
