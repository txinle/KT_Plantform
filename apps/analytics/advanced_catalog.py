"""Eligibility of optional modules for existing export API clients."""

from apps.experiments.models import Experiment


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
