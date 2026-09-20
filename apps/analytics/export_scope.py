from dataclasses import dataclass
from urllib.parse import urlencode

from apps.accounts.models import ClassGroup
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
)


class ExportScopeValidationError(ValueError):
    pass


def published_class_ids_for_experiment(experiment_id):
    task_ids = tuple(
        ClassExperimentTask.objects.filter(experiment_id=experiment_id)
        .order_by("class_group_id")
        .values_list("class_group_id", flat=True)
    )
    if task_ids:
        return task_ids
    return tuple(
        ExperimentClassSnapshot.objects.filter(
            version__experiment_id=experiment_id,
            source_class_group_id__isnull=False,
        )
        .order_by("source_class_group_id")
        .values_list("source_class_group_id", flat=True)
        .distinct()
    )


@dataclass(frozen=True)
class ExperimentExportScope:
    course: object
    experiment_id: int
    class_mode: str
    class_id: int | None = None

    SCOPE_VERSION = 2
    CLASS_MODES = frozenset({"single", "all"})

    @classmethod
    def from_mapping(cls, course, values=None):
        values = values or {}
        raw_ids = values.get("experiment_ids") or ()
        if isinstance(raw_ids, str):
            raw_ids = [item for item in raw_ids.split(",") if item]
        experiment_id = values.get("experiment_id")
        if raw_ids:
            try:
                ids = tuple(dict.fromkeys(int(item) for item in raw_ids))
            except (TypeError, ValueError) as error:
                raise ExportScopeValidationError("实验编号无效") from error
            if len(ids) != 1:
                raise ExportScopeValidationError("一次只能导出一个实验")
            if experiment_id not in (None, "", ids[0], str(ids[0])):
                raise ExportScopeValidationError("一次只能导出一个实验")
            experiment_id = ids[0]
        if experiment_id in (None, ""):
            raise ExportScopeValidationError("必须选择一个实验")
        try:
            experiment_id = int(experiment_id)
        except (TypeError, ValueError) as error:
            raise ExportScopeValidationError("实验编号无效") from error
        if not Experiment.objects.filter(pk=experiment_id, course=course).exists():
            raise ExportScopeValidationError("实验不属于当前课程")
        published_class_ids = published_class_ids_for_experiment(experiment_id)
        if not published_class_ids:
            raise ExportScopeValidationError("当前实验尚未发布到任何班级")

        class_mode = values.get("class_mode")
        class_id = values.get("class_id")
        if class_mode in (None, ""):
            class_mode = "single" if class_id not in (None, "") else None
        if class_mode not in cls.CLASS_MODES:
            raise ExportScopeValidationError("必须选择班级范围")
        if class_mode == "all":
            if class_id not in (None, ""):
                raise ExportScopeValidationError("全部班级范围不能指定班级")
            return cls(course=course, experiment_id=experiment_id, class_mode="all")

        if class_id in (None, ""):
            raise ExportScopeValidationError("单班级范围必须指定班级")
        try:
            class_id = int(class_id)
        except (TypeError, ValueError) as error:
            raise ExportScopeValidationError("班级编号无效") from error
        if not ClassGroup.objects.filter(pk=class_id, course=course).exists():
            raise ExportScopeValidationError("班级不属于当前课程")
        published = class_id in published_class_ids
        if not published:
            raise ExportScopeValidationError("班级未发布当前实验")
        return cls(
            course=course,
            experiment_id=experiment_id,
            class_mode="single",
            class_id=class_id,
        )

    @property
    def is_complete(self):
        return True

    def serialize(self):
        values = {
            "scope_version": self.SCOPE_VERSION,
            "experiment_id": self.experiment_id,
            "class_mode": self.class_mode,
        }
        if self.class_id is not None:
            values["class_id"] = self.class_id
        return values

    def query(self):
        return urlencode(self.serialize())

    def published_class_ids(self):
        return published_class_ids_for_experiment(self.experiment_id)

    def selected_class_ids(self):
        return (
            self.published_class_ids()
            if self.class_mode == "all"
            else (self.class_id,)
        )

    def display(self):
        experiment = Experiment.objects.get(pk=self.experiment_id)
        if self.class_mode == "all":
            return f"{experiment.name} · 全部已发布班级"
        class_group = ClassGroup.objects.get(pk=self.class_id)
        return f"{experiment.name} · {class_group.name}"
