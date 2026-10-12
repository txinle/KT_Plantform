"""The user-approved eight-file research exchange contract."""

import csv
from pathlib import Path


FILES = [
    {"key": "course_students", "path": "01_课程学生表.csv", "label": "课程学生表", "level": "course", "headers": "课程 学号 姓名".split()},
    {"key": "session_bindings", "path": "02_学生Session实验绑定表.csv", "label": "学生Session实验绑定表", "level": "experiment_binding", "headers": "课程 学号 姓名 班级 实验序号 实验名称 sessionID".split()},
    {"key": "course_knowledge", "path": "03_课程知识点表.csv", "label": "课程知识点表", "level": "course", "headers": "课程 KC编号 知识点名称".split()},
    {"key": "course_mapping", "path": "04_题目KC映射表.csv", "label": "题目KC映射表", "level": "course", "headers": "课程 课程内题目ID 一级KC编号 二级KC编号 三级KC继承映射".split()},
    {"key": "responses", "path": "05_单次实验_逐题作答表.csv", "label": "逐题作答表", "level": "experiment", "headers": "sessionID 学号 姓名 课程内题目ID 分配顺序 首次访问顺序 选项呈现顺序 访问次数 各次点击题目时间 各次进入题目时间 各次离开题目时间 正式提交时间 最终答案 答案状态 是否正确".split()},
    {"key": "behavior_events", "path": "06_单次实验_行为过程表.csv", "label": "行为过程表", "level": "experiment", "headers": "sessionID 学号 姓名 课程内题目ID 第几次访问 题内行为序号 行为类别 具体行为 发生时间 变化前内容 变化后内容 补充信息".split()},
    {"key": "event_dictionary", "path": "07_表6事件字典.csv", "label": "表6事件字典", "level": "dictionary", "headers": "行为类别 具体行为 适用对象 何时记录 变化前内容 变化后内容 补充信息建议 是否建议默认采集".split()},
    {"key": "question_library", "path": "课程题目固定库.json", "label": "课程题目固定库", "level": "course", "headers": []},
]


def event_dictionary():
    from .research_events import STANDARD_EVENTS, TOOL_ACTIONS, FEEDBACK_ACTIONS, _INPUT_TYPE_LABELS, _RESEARCH_TOOL_LABELS, _RESEARCH_TOOL_STAGE_LABELS

    with Path(__file__).with_name("research_event_dictionary.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))[1:]
    seen = {(row[0], row[1]) for row in rows}
    additions = list(STANDARD_EVENTS.values()) + [("输入操作", action) for action in _INPUT_TYPE_LABELS.values()]
    additions += [("输入操作", "操作请求"), ("文本输入", "文本编辑记录")]
    additions += [("研究工具", action) for action in TOOL_ACTIONS.values()]
    additions += [("研究工具", f"{tool}：{stage}") for tool in _RESEARCH_TOOL_LABELS.values() for stage in _RESEARCH_TOOL_STAGE_LABELS.values()]
    additions += [("反馈/解析", action) for action in FEEDBACK_ACTIONS.values()] + [("题目访问", "跳过题目")]
    for category, action in additions:
        if (category, action) not in seen:
            rows.append([category, action, "实际启用功能的题内记录", "系统收到对应真实事件时", "有明确状态证据才填写", "有明确状态证据才填写",
                "保留原始事件类型、载荷、时间依据；输入操作请求与答案状态变化分别记录；无法确定访问时留空", "按实际启用功能"])
            seen.add((category, action))
    return rows


def file_catalog(dataset=None):
    return [
        {"key": item["key"], "path": item["path"], "label": item["label"], "level": item["level"],
         "format": "json" if item["key"] == "question_library" else "csv",
         "description": {"course": "课程全量基础资料，不随班级筛选裁剪", "experiment_binding": "当前实验全部已发布班级的 Session 绑定", "experiment": "当前研究范围内的原始记录", "dictionary": "行为过程的记录语义与时间规范"}[item["level"]],
         "total_items": len(dataset[item["key"]]) if dataset else 0}
        for item in FILES
    ]
