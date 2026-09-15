import json
import os

from django.core.management.base import BaseCommand

from config.gunicorn import effective_cpu_count, effective_memory_bytes, server_worker_count


def _positive_int_override(name, fallback):
    value = os.getenv(name, "").strip()
    return max(1, int(value)) if value else fallback


def capacity_report():
    replicas = max(1, int(os.getenv("WEB_REPLICAS", "1")))
    workers_per_replica = server_worker_count()
    cpu_per_replica = effective_cpu_count()
    memory_per_replica = effective_memory_bytes()
    worker_memory_mb = max(
        256, int(os.getenv("GUNICORN_MEMORY_PER_WORKER_MB", "512"))
    )
    total_workers = replicas * workers_per_replica
    aggregate_cpu = _positive_int_override(
        "DEPLOYMENT_AGGREGATE_VCPU", cpu_per_replica
    )
    aggregate_memory_bytes = _positive_int_override(
        "DEPLOYMENT_AGGREGATE_MEMORY_MB", memory_per_replica // 1024**2
    ) * 1024**2
    memory_worker_capacity = (
        aggregate_memory_bytes // (worker_memory_mb * 1024**2)
    )
    checks = {
        "at_least_two_web_instances": replicas >= 2,
        "at_least_sixteen_web_workers": total_workers >= 16,
        "at_least_sixteen_aggregate_vcpu": aggregate_cpu >= 16,
        "memory_supports_configured_workers": memory_worker_capacity >= total_workers,
        "transaction_pool_enabled": os.getenv("POSTGRES_HOST", "") == "pgbouncer",
    }
    return {
        "source": "server_cgroup_and_deployment_environment",
        "web_replicas": replicas,
        "cpu_per_replica": cpu_per_replica,
        "memory_bytes_per_replica": memory_per_replica,
        "workers_per_replica": workers_per_replica,
        "total_web_workers": total_workers,
        "aggregate_vcpu": aggregate_cpu,
        "aggregate_memory_bytes": aggregate_memory_bytes,
        "worker_memory_budget_mb": worker_memory_mb,
        "pgbouncer_default_pool_size": int(
            os.getenv("PGBOUNCER_DEFAULT_POOL_SIZE", "40")
        ),
        "checks": checks,
        "eligible_for_300_user_acceptance": all(checks.values()),
    }


class Command(BaseCommand):
    help = "Report 300-user capacity from the deployed server/container limits."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(capacity_report(), ensure_ascii=False, indent=2))
