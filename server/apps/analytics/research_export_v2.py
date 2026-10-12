import base64
import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
import zlib
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from apps.accounts.models import CourseEnrollment
from apps.assessment.models import ExperimentSession
from apps.question_bank.models import Concept, Question
from apps.question_bank.services.question_kc_mapping import (
    project_question_kc_mapping,
    question_kc_mapping_prefetch,
)

from .models import (
    ExportSnapshot,
    ResearchExportAsset,
    ResearchSessionBehavior,
    ResearchSessionMaterialization,
    ResearchSessionResponse,
)
from .export_scope import published_class_ids_for_experiment
from .research_contract import FILES, event_dictionary


CONTRACT_VERSION = "research-export-v2"


class ResearchExportNotReady(ValueError):
    pass


class ResearchExportAssetError(ValueError):
    pass


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _content_hash(files):
    digest = hashlib.sha256()
    for path in sorted(files):
        body = files[path]
        digest.update(len(path.encode("utf-8")).to_bytes(4, "big"))
        digest.update(path.encode("utf-8"))
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest()


def _write_asset(kind, course, experiment, files, row_counts, *, job=None):
    from .services import _export_file_lock
    with _export_file_lock('asset-' + _content_hash(files)):
        return _write_asset_unlocked(kind, course, experiment, files, row_counts, job=job)


def _write_asset_unlocked(kind, course, experiment, files, row_counts, *, job=None):
    content_hash = _content_hash(files)
    existing = ResearchExportAsset.objects.filter(
        kind=kind,
        course=course,
        experiment=experiment,
        contract_version=CONTRACT_VERSION,
        content_hash=content_hash,
    ).first()
    if existing:
        return existing
    scope_key = str(experiment.pk) if experiment is not None else "course"
    root = os.path.join(
        settings.MEDIA_ROOT,
        "research-assets",
        kind,
        str(course.pk),
        scope_key,
    )
    os.makedirs(root, exist_ok=True)
    final_path = os.path.join(root, f"{content_hash}.zip")
    temporary_path = None
    try:
        if not os.path.exists(final_path):
            handle, temporary_path = tempfile.mkstemp(prefix="asset-", suffix=".tmp", dir=root)
            os.close(handle)
            with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(files):
                    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, files[path])
            with zipfile.ZipFile(temporary_path) as archive:
                digest = hashlib.sha256()
                for name in sorted(archive.namelist()):
                    digest.update(len(name.encode('utf-8')).to_bytes(4, 'big'))
                    digest.update(name.encode('utf-8'))
                    digest.update(archive.getinfo(name).file_size.to_bytes(8, 'big'))
                    with archive.open(name) as body:
                        for chunk in iter(lambda: body.read(1024 * 1024), b''):
                            digest.update(chunk)
            if digest.hexdigest() != content_hash:
                raise ResearchExportAssetError("研究资产临时文件内容校验失败")
        from .services import _file_sha256, ExportContractError
        from apps.accounts.course_access import lock_course
        from .models import ExportJob
        artifact_sha256 = _file_sha256(temporary_path or final_path)
        with transaction.atomic():
            lock_course(course.pk)
            if job and not ExportJob.objects.filter(pk=job.pk, status=ExportJob.Status.RUNNING,
                    locked_by=job.locked_by, lease_expires_at__gt=timezone.now(),
                    snapshot__invalidated_at=None, snapshot__redacted_at=None).filter(
                        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).exists():
                raise ExportContractError('导出源已失效或租约丢失，研究资产未发布。')
            if temporary_path:
                os.replace(temporary_path, final_path)
            asset, _created = ResearchExportAsset.objects.get_or_create(
                kind=kind, course=course, experiment=experiment,
                contract_version=CONTRACT_VERSION, content_hash=content_hash,
                defaults={"artifact_path": final_path, "artifact_sha256": artifact_sha256,
                    "row_counts": row_counts},
            )
        return asset
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)



def _course_asset_files(course):
    roster = {}
    for enrollment in (
        CourseEnrollment.objects.filter(course=course)
        .select_related("student")
        .order_by("student_id", "pk")
    ):
        roster.setdefault(
            enrollment.student_id,
            [
                course.name,
                enrollment.student.student_number or "",
                enrollment.student.shown_name,
            ],
        )
    knowledge = [
        [course.name, item.code, item.name]
        for item in Concept.objects.filter(course=course).order_by("code", "pk")
    ]
    mappings = []
    library = []
    questions = (
        Question.objects.filter(bank__course=course)
        .select_related("course_identity")
        .prefetch_related("options", question_kc_mapping_prefetch())
        .order_by("course_identity__course_sequence", "pk")
    )
    for question in questions:
        sequence = question.course_identity.course_sequence
        question_ref = f"Q{sequence:03d}"
        nodes = project_question_kc_mapping(question)["nodes"]
        mappings.append(
            [
                course.name,
                question_ref,
                ";".join(node["kc_code"] for node in nodes if node["depth"] == 1),
                ";".join(node["kc_code"] for node in nodes if node["depth"] == 2),
                ";".join(
                    f"{node['parent_kc_code']}>{node['kc_code']}"
                    for node in nodes
                    if node["depth"] == 3
                ),
            ]
        )
        library.append(
            {
                "题号": question_ref,
                "题干": question.text,
                "选项": {item.key: item.text for item in question.options.all()},
                "答案": question.correct_answer,
                "解析": question.explanation,
                "难度": (
                    float(question.teacher_difficulty)
                    if question.teacher_difficulty is not None
                    else None
                ),
            }
        )
    rows = {
        "course_students": list(roster.values()),
        "course_knowledge": knowledge,
        "course_mapping": mappings,
        "event_dictionary": event_dictionary(),
        "question_library": library,
    }
    contracts = {item["key"]: item for item in FILES}
    files = {}
    for key, values in rows.items():
        contract = contracts[key]
        files[contract["path"]] = (
            _json_bytes(values)
            if key == "question_library"
            else _csv_bytes(contract["headers"], values)
        )
    return files, {key: len(values) for key, values in rows.items()}


def get_or_create_course_asset(course):
    files, row_counts = _course_asset_files(course)
    return _write_asset(
        ResearchExportAsset.Kind.COURSE,
        course,
        None,
        files,
        row_counts,
    )


def _session_reference(session):
    return f"S{session.started_at.astimezone(ZoneInfo('Asia/Shanghai')):%Y%m%d}_{session.pk:04d}"


def get_or_create_experiment_session_asset(experiment):
    files, row_counts = _experiment_session_asset_files(experiment)
    return _write_asset(ResearchExportAsset.Kind.EXPERIMENT_SESSIONS, experiment.course, experiment, files, row_counts)


def _experiment_session_asset_files(experiment):
    from .research_materialization import PROJECTION_VERSION
    published_class_ids = published_class_ids_for_experiment(experiment.pk)
    current = (
        ResearchSessionMaterialization.objects.filter(
            session__version__experiment=experiment,
            session__class_snapshot__source_class_group_id__in=published_class_ids,
            session__status__in=(
                ExperimentSession.Status.COMPLETED,
                ExperimentSession.Status.EXPIRED,
            ),
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            source_revision=F("session__research_source_revision"),
        )
        .select_related("session__student", "session__class_snapshot")
        .only("session_summary", "session_id", "session__started_at", "session__class_snapshot_name",
              "session__student__student_number", "session__student__display_name",
              "session__student__username", "session__student__first_name", "session__student__last_name",
              "session__class_snapshot")
        .order_by("session_id")
    )
    rows = []
    for item in current:
        session = item.session
        rows.append(
            [
                experiment.course.name,
                session.student.student_number or "",
                session.student.shown_name,
                session.class_snapshot_name,
                experiment.pk,
                experiment.name,
                item.session_summary.get("session_ref") or _session_reference(session),
            ]
        )
    contract = next(item for item in FILES if item["key"] == "session_bindings")
    files = {contract["path"]: _csv_bytes(contract["headers"], rows)}
    return files, {"session_bindings": len(rows)}


def _json_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()


def _terminal_sessions(experiment, class_ids):
    return ExperimentSession.objects.filter(
        version__experiment=experiment,
        class_snapshot__source_class_group_id__in=class_ids,
        status__in=(
            ExperimentSession.Status.COMPLETED,
            ExperimentSession.Status.EXPIRED,
        ),
    )


def create_research_export_snapshot(*, course, experiment, created_by, scope):
    """Freeze only immutable asset and materialization references for export v2."""
    from apps.accounts.course_access import lock_course
    from .research_materialization import PROJECTION_VERSION

    if scope.course.pk != course.pk or scope.experiment_id != experiment.pk:
        raise ResearchExportAssetError("导出范围与课程或实验不一致")

    owns_transaction = not connection.in_atomic_block
    with transaction.atomic():
        if owns_transaction and connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        lock_course(course.pk)
        all_terminal = _terminal_sessions(
            experiment, tuple(scope.published_class_ids())
        )
        all_current = ResearchSessionMaterialization.objects.filter(
            session__in=all_terminal,
            status=ResearchSessionMaterialization.Status.SUCCEEDED,
            is_current=True,
            projection_version=PROJECTION_VERSION,
            source_revision=F("session__research_source_revision"),
        ).order_by("session_id", "created_at")
        pending_count = all_terminal.annotate(
            has_current=Exists(all_current.filter(session_id=OuterRef('pk')))
        ).filter(has_current=False).count()
        if pending_count:
            raise ResearchExportNotReady(
                f"{pending_count} 名已结束学生的数据正在后台整理，请稍后再导出"
            )

        selected_terminal = _terminal_sessions(
            experiment, tuple(scope.selected_class_ids())
        )
        current = all_current.filter(session__in=selected_terminal)
        materializations = list(current.values("materialization_id", "source_revision").iterator(chunk_size=1000))
        materialization_ids = [item['materialization_id'] for item in materializations]

        course_files, course_counts = _course_asset_files(course)
        session_files, session_counts = _experiment_session_asset_files(experiment)
        cutoff = timezone.now()
        filter_definition = scope.serialize()
        source_bundle = {
            "version": 2,
            "research_export_v2": {
                "contract_version": CONTRACT_VERSION,
                "course_asset_id": None,
                "course_asset_hash": _content_hash(course_files),
                "experiment_session_asset_id": None,
                "experiment_session_asset_hash": _content_hash(session_files),
                "deferred_assets": {
                    "course": {"files_base64": {path: base64.b64encode(body).decode("ascii") for path, body in course_files.items()}, "row_counts": course_counts},
                    "sessions": {"files_base64": {path: base64.b64encode(body).decode("ascii") for path, body in session_files.items()}, "row_counts": session_counts},
                },
                "materialization_ids": [str(pk) for pk in materialization_ids],
                "materializations": [
                    {
                        "id": str(item["materialization_id"]),
                        "source_revision": item["source_revision"],
                    }
                    for item in materializations
                ],
                "class_mode": scope.class_mode,
                "class_id": scope.class_id,
            },
        }
        frozen = source_bundle['research_export_v2']
        for key, kind, asset_experiment, hash_key, id_key in (
            ('course', ResearchExportAsset.Kind.COURSE, None, 'course_asset_hash', 'course_asset_id'),
            ('sessions', ResearchExportAsset.Kind.EXPERIMENT_SESSIONS, experiment,
             'experiment_session_asset_hash', 'experiment_session_asset_id'),
        ):
            asset = ResearchExportAsset.objects.filter(kind=kind, course=course, experiment=asset_experiment,
                contract_version=CONTRACT_VERSION, content_hash=frozen[hash_key]).first()
            if asset and os.path.isfile(asset.artifact_path):
                frozen[id_key] = str(asset.pk)
                frozen['deferred_assets'].pop(key)
        versions = list(
            experiment.versions.order_by("version_number").values_list(
                "pk", "version_number"
            )
        )
        return ExportSnapshot.objects.create(
            course=course,
            experiment=experiment,
            created_by=created_by,
            cutoff_at=cutoff,
            source_cutoff_at=cutoff,
            database_snapshot_strategy="materialized_references_v2",
            selected_versions=[
                {"id": pk, "version": version} for pk, version in versions
            ],
            filter_definition=filter_definition,
            scope_hash=_json_hash(filter_definition),
            source_bundle=source_bundle,
            source_bundle_hash=_json_hash(source_bundle),
        )


def _verified_asset(asset_id, expected_hash):
    try:
        asset = ResearchExportAsset.objects.get(pk=asset_id)
    except ResearchExportAsset.DoesNotExist as error:
        raise ResearchExportAssetError("冻结研究资产不存在") from error
    if asset.content_hash != expected_hash or not os.path.isfile(asset.artifact_path):
        raise ResearchExportAssetError("冻结研究资产已损坏或不匹配")
    with open(asset.artifact_path, "rb") as stream:
        digest_state = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest_state.update(chunk)
        digest = digest_state.hexdigest()
    if digest != asset.artifact_sha256:
        raise ResearchExportAssetError("冻结研究资产文件校验失败")
    return asset


def _identity_map(session_asset):
    contract = next(item for item in FILES if item["key"] == "session_bindings")
    with zipfile.ZipFile(session_asset.artifact_path) as archive:
        with archive.open(contract["path"]) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                rows = csv.reader(stream)
                next(rows, None)
                return {row[-1]: (row[1], row[2]) for row in rows}


def _copy_asset_into_export(asset, target):
    names = set()
    with zipfile.ZipFile(asset.artifact_path) as source:
        for name in source.namelist():
            if name in names:
                raise ResearchExportAssetError("冻结研究资产包含重复文件")
            with source.open(name) as input_stream, target.open(name, "w") as output:
                shutil.copyfileobj(input_stream, output, length=1024 * 1024)
            names.add(name)
    return names


def _write_fact_csv(
    archive, *, contract, model, materialization_ids, identities
):
    with archive.open(contract["path"], "w") as raw:
        with io.TextIOWrapper(
            raw, encoding="utf-8-sig", newline="", write_through=True
        ) as stream:
            writer = csv.writer(stream)
            writer.writerow(contract["headers"])
            for offset in range(0, len(materialization_ids), 1000):
                queryset = model.objects.filter(
                    materialization_id__in=materialization_ids[offset:offset + 1000]
                ).order_by("materialization__session_id", "ordinal", "pk").only('row')
                for item in queryset.iterator(chunk_size=1000):
                    row = list(item.row)
                    identity = identities.get(str(row[0])) if row else None
                    if identity and len(row) >= 3:
                        row[1], row[2] = identity
                    writer.writerow(row)


def materialize_research_export(job):
    frozen = job.snapshot.source_bundle.get("research_export_v2", {})
    deferred = frozen.get("deferred_assets")
    assets = []
    for key, kind, experiment, hash_key, id_key in (
        ('course', ResearchExportAsset.Kind.COURSE, None, 'course_asset_hash', 'course_asset_id'),
        ('sessions', ResearchExportAsset.Kind.EXPERIMENT_SESSIONS, job.experiment,
         'experiment_session_asset_hash', 'experiment_session_asset_id'),
    ):
        source = (deferred or {}).get(key)
        if source:
            if 'files_base64' in source:
                files = {path: base64.b64decode(body) for path, body in source['files_base64'].items()}
            else:
                files = {path: zlib.decompress(base64.b64decode(body)) for path, body in source["files_zlib"].items()}
            if _content_hash(files) != frozen[hash_key]:
                raise ResearchExportAssetError("冻结研究资产内容校验失败")
            asset = _write_asset(kind, job.course, experiment, files, source["row_counts"], job=job)
            assets.append(_verified_asset(asset.pk, frozen[hash_key]))
        else:
            assets.append(_verified_asset(frozen.get(id_key), frozen.get(hash_key)))
    course_asset, session_asset = assets
    identities = _identity_map(session_asset)
    materialization_ids = frozen.get("materialization_ids", [])
    contracts = {item["key"]: item for item in FILES}
    expected_paths = {item["path"] for item in FILES}

    root = os.path.join(settings.MEDIA_ROOT, "exports", str(job.pk))
    os.makedirs(root, exist_ok=True)
    from .services import _export_candidate_path
    artifact_path = _export_candidate_path(job, root, "zip")
    handle, temporary_path = tempfile.mkstemp(
        prefix="export-", suffix=".tmp", dir=root
    )
    os.close(handle)
    try:
        with zipfile.ZipFile(
            temporary_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            names = _copy_asset_into_export(course_asset, archive)
            session_names = _copy_asset_into_export(session_asset, archive)
            if names.intersection(session_names):
                raise ResearchExportAssetError("冻结研究资产文件集合重复")
            names.update(session_names)
            for key, model in (
                ("responses", ResearchSessionResponse),
                ("behavior_events", ResearchSessionBehavior),
            ):
                contract = contracts[key]
                _write_fact_csv(
                    archive,
                    contract=contract,
                    model=model,
                    materialization_ids=materialization_ids,
                    identities=identities,
                )
                names.add(contract["path"])
            if names != expected_paths:
                raise ResearchExportAssetError(
                    "冻结研究资产文件集合与契约不一致"
                )
        os.replace(temporary_path, artifact_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return artifact_path
