"""Semantic navigation for advanced research data tools."""

from __future__ import annotations

from apps.experiments.models import Experiment

from .export_catalog import TABLE_LABELS, TABLE_PATHS


GROUP_DEFINITIONS = (
    {
        "key": "experiment_participants",
        "label": "实验与参与者",
        "description": "实验会话、参与者与发布时冻结的设计证据。",
        "tables": (
            "subjects",
            "sessions",
            "condition_assignments",
            "class_snapshots",
            "participant_snapshots",
        ),
    },
    {
        "key": "questions_knowledge",
        "label": "题目与知识结构",
        "description": "题目分配、呈现实例与权威 Item–KC 关系。",
        "tables": (
            "assignments",
            "item_instances",
            "item_snapshots",
            "kc_dictionary",
            "item_kc_mapping",
        ),
    },
    {
        "key": "responses_grading",
        "label": "作答与评分",
        "description": "作答、答案状态、提交事实与版本化评分记录。",
        "tables": (
            "attempts",
            "response_snapshots",
            "submissions",
            "grade_records",
        ),
    },
    {
        "key": "behavior_process",
        "label": "行为过程",
        "description": "不可变总事件表及从总表生成的用途分类视图。",
        "tables": (
            "assessment_events",
            "response_events",
            "navigation_events",
            "exposure_events",
            "telemetry_events",
            "response_input_observations",
        ),
    },
    {
        "key": "extensions",
        "label": "扩展观测",
        "description": "仅在当前实验启用相应采集模块时显示。",
        "tables": (
            "confidence_assignments",
            "simulation_events",
            "simulation_snapshots",
        ),
    },
    {
        "key": "derived",
        "label": "派生研究数据",
        "description": "由权威事实和原始观测按版本化规则可复现生成。",
        "tables": (
            "q_matrix",
            "attempt_features",
            "kt_sequences",
            "cd_response_matrix",
            "calibration_features",
        ),
    },
)


TABLE_ROLES = {
    "assessment_events": (
        "source_of_truth",
        "权威总事件表 · Source of Truth",
        "所有行为事件的权威、不可变总表。",
    ),
    "response_events": (
        "classified_view",
        "分类视图",
        "从 assessment_events 按作答事件类型筛选得到。",
    ),
    "navigation_events": (
        "classified_view",
        "分类视图",
        "从 assessment_events 按导航事件类型筛选得到。",
    ),
    "exposure_events": (
        "classified_view",
        "分类视图",
        "从 assessment_events 按学习暴露事件类型筛选得到。",
    ),
    "telemetry_events": (
        "classified_view",
        "分类视图",
        "从 assessment_events 按页面遥测事件类型筛选得到。",
    ),
    "simulation_events": (
        "classified_view",
        "分类视图",
        "从 assessment_events 按仿真事件类型筛选得到。",
    ),
    "response_input_observations": (
        "classified_view",
        "事件投影视图",
        "由 assessment_events 中的 text_edit_observed 物化得到的结构化投影。",
    ),
}


SOURCE_LAYERS = {
    "subjects": "权威实验事实",
    "sessions": "权威实验事实",
    "assignments": "权威实验事实",
    "item_instances": "权威实验事实",
    "attempts": "权威实验事实",
    "submissions": "权威实验事实",
    "grade_records": "权威实验事实",
    "condition_assignments": "实验设计元数据",
    "class_snapshots": "实验设计元数据",
    "participant_snapshots": "实验设计元数据",
    "item_snapshots": "实验设计元数据",
    "kc_dictionary": "实验设计元数据",
    "item_kc_mapping": "实验设计元数据",
    "confidence_assignments": "扩展观测",
    "q_matrix": "可复现派生",
    "attempt_features": "可复现派生",
    "kt_sequences": "可复现派生",
    "cd_response_matrix": "可复现派生",
    "calibration_features": "可复现派生",
}


DEFAULT_ROLE = ("research_table", "研究数据表", "当前范围内的结构化研究数据。")


def enabled_extension_tables(experiment):
    if experiment is None:
        return set()
    enabled = set()
    if experiment.confidence_mode != Experiment.ConfidenceMode.OFF:
        enabled.add("confidence_assignments")
    if any(
        bool(version.configuration.get("simulation_enabled"))
        for version in experiment.versions.only("configuration")
    ):
        enabled.update(("simulation_events", "simulation_snapshots"))
    return enabled


def build_advanced_catalog(*, experiment, selected_table):
    """Return six stable groups, hiding disabled optional observation modules."""
    enabled_extensions = enabled_extension_tables(experiment)
    visible_keys = {
        table_key
        for group in GROUP_DEFINITIONS
        for table_key in group["tables"]
        if group["key"] != "extensions" or table_key in enabled_extensions
    }
    if selected_table not in visible_keys:
        selected_table = "attempts"

    groups = []
    selected_item = None
    for definition in GROUP_DEFINITIONS:
        tables = []
        for table_key in definition["tables"]:
            if table_key not in visible_keys:
                continue
            role, role_label, description = TABLE_ROLES.get(table_key, DEFAULT_ROLE)
            item = {
                "key": table_key,
                "label": TABLE_LABELS.get(table_key, table_key),
                "path": TABLE_PATHS[table_key],
                "role": role,
                "role_label": role_label,
                "description": description,
                "schema_version": "v2",
                "source_layer": SOURCE_LAYERS.get(table_key, "原始科研观测"),
                "active": table_key == selected_table,
            }
            if item["active"]:
                selected_item = item
            tables.append(item)
        groups.append(
            {
                **definition,
                "tables": tables,
                "active": any(table["active"] for table in tables),
            }
        )
    return groups, selected_table, selected_item
