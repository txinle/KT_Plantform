import json
import os

from django.core.management.base import BaseCommand

from config.gunicorn import effective_cpu_count, effective_memory_bytes, server_worker_count


def capacity_report():
    school = os.getenv("SCHOOL_RUNTIME_ISOLATION_ENABLED", "0") == "1"
    replicas = max(1, int(os.getenv("WEB_REPLICAS", "1")))
    workers = server_worker_count()
    cpu = effective_cpu_count()
    memory = effective_memory_bytes()
    aggregate_cpu = os.getenv("DEPLOYMENT_AGGREGATE_VCPU", "").strip()
    aggregate_memory = os.getenv("DEPLOYMENT_AGGREGATE_MEMORY_MB", "").strip()
    roles = {"core": workers * replicas}
    profile = os.getenv("SCHOOL_POOL_PROFILE", "").strip()
    six = school and profile == "six-pools-180-v1"
    observed_alias = os.getenv("POSTGRES_POOL_ALIAS", "")
    configured_aliases = {"kt_core": 1, "kt_auth": 4, "kt_assessment": 24,
                          "kt_capture": 4, "kt_student": 2, "kt_teacher": 1}
    if school:
        roles.update(student_read=2, teacher_read=1)
    if six:
        roles = {"core": 1, "auth": 4, "assessment": 24, "capture": 4, "student_read": 2, "teacher_read": 1}
    checks = {
        "transaction_pool_enabled": os.getenv("POSTGRES_HOST", "") == "pgbouncer",
        "school_core_configuration": not school or (replicas == 1 and
            (workers == configured_aliases.get(observed_alias) if six else workers == 12)),
        "known_pool_profile": not school or profile in ("", "six-pools-180-v1"),
    }
    return {
        "source": "server_cgroup_and_deployment_environment",
        "observed_role": os.getenv("DATABASE_APPLICATION_NAME", "kt-web"),
        "cpu_per_replica": cpu,
        "memory_bytes_per_replica": memory,
        "aggregate_vcpu": int(aggregate_cpu) if aggregate_cpu else cpu,
        "aggregate_memory_bytes": int(aggregate_memory) * 1024**2 if aggregate_memory else memory,
        "web_replicas": replicas,
        "workers_per_replica": workers,
        "role_workers": roles,
        "pool_profile": profile or None,
        "configured_http_worker_total": sum(roles.values()),
        "observed_role_workers": workers * replicas,
        "role_workers_source": "current_container_plus_school_overlay_configuration" if school else "current_container_environment",
        "other_role_containers_observed": False,
        "database_pool_alias": observed_alias,
        "checks": checks,
        "ready_for_measurement": all(checks.values()),
        "accepted_for_300_users": False,
        "acceptance_evidence": ("Run both staged 180-student protocol and browser rounds; configured worker totals are not observed capacity or 300-user acceptance evidence."
            if six else "Run the 300-student HTTP and 30-minute mixed-load harnesses; configuration arithmetic is not acceptance evidence."),
    }


class Command(BaseCommand):
    help = "Report runtime preflight evidence; this command never certifies 300-user capacity."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(capacity_report(), ensure_ascii=False, indent=2))
