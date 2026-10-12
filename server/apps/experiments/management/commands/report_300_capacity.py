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
    if school:
        roles.update(student_read=2, teacher_read=1)
    checks = {
        "transaction_pool_enabled": os.getenv("POSTGRES_HOST", "") == "pgbouncer",
        "school_core_configuration": not school or (workers == 12 and replicas == 1),
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
        "role_workers_source": "current_container_plus_school_overlay_configuration" if school else "current_container_environment",
        "other_role_containers_observed": False,
        "database_pool_alias": os.getenv("POSTGRES_POOL_ALIAS", ""),
        "checks": checks,
        "ready_for_measurement": all(checks.values()),
        "accepted_for_300_users": False,
        "acceptance_evidence": "Run the 300-student HTTP and 30-minute mixed-load harnesses; configuration arithmetic is not acceptance evidence.",
    }


class Command(BaseCommand):
    help = "Report runtime preflight evidence; this command never certifies 300-user capacity."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(capacity_report(), ensure_ascii=False, indent=2))
