PACKAGE_TABLES = {
    "complete": (
        "subjects", "sessions", "assignments", "item_instances", "attempts",
        "assessment_events", "response_events", "navigation_events",
        "exposure_events", "telemetry_events", "item_snapshots",
        "kc_dictionary", "item_kc_mapping", "q_matrix",
        "confidence_assignments", "attempt_features", "kt_sequences",
        "cd_response_matrix", "calibration_features",
    ),
    "kt": ("subjects", "attempts", "item_kc_mapping", "kt_sequences"),
    "cd": ("subjects", "attempts", "q_matrix", "cd_response_matrix"),
    "process": (
        "subjects", "sessions", "assignments", "item_instances", "attempts",
        "assessment_events", "response_events", "navigation_events",
        "exposure_events", "telemetry_events",
    ),
    "confidence": (
        "subjects", "attempts", "confidence_assignments", "calibration_features",
    ),
    "simulation": ("subjects", "attempts", "simulation_events", "simulation_snapshots"),
    "longitudinal": ("subjects", "sessions", "attempts", "assessment_events", "kt_sequences"),
}

TABLE_PATHS = {
    "subjects": "02_core/subjects.csv",
    "sessions": "02_core/sessions.csv",
    "assignments": "02_core/assignments.csv",
    "item_instances": "02_core/item_instances.csv",
    "attempts": "02_core/attempts.csv",
    "assessment_events": "03_raw_events/assessment_events.csv",
    "response_events": "03_raw_events/response_events.csv",
    "navigation_events": "03_raw_events/navigation_events.csv",
    "exposure_events": "04_learning_exposure/exposure_events.csv",
    "telemetry_events": "03_raw_events/telemetry_events.csv",
    "simulation_events": "03_raw_events/simulation_events.csv",
    "simulation_snapshots": "03_raw_events/simulation_snapshots.csv",
    "item_snapshots": "01_design_metadata/item_snapshots.csv",
    "kc_dictionary": "01_design_metadata/kc_dictionary.csv",
    "item_kc_mapping": "01_design_metadata/item_kc_mapping.csv",
    "q_matrix": "01_design_metadata/q_matrix.csv",
    "confidence_assignments": "05_self_report/confidence_assignments.csv",
    "attempt_features": "06_derived/attempt_features.csv",
    "kt_sequences": "06_derived/kt_sequences.csv",
    "cd_response_matrix": "06_derived/cd_response_matrix.csv",
    "calibration_features": "06_derived/calibration_features.csv",
}

RESPONSE_EVENT_TYPES = {
    "response_input_started", "response_state_changed", "response_commit_requested",
    "response_committed", "response_commit_rejected", "attempt_closed",
}
EXPOSURE_EVENT_TYPES = {
    "feedback_shown", "explanation_shown", "hint_opened", "hint_closed",
    "resource_opened", "resource_closed",
}
SIMULATION_EVENT_TYPES = {
    "simulation_parameter_changed", "simulation_run_started",
    "simulation_run_completed", "simulation_error_returned",
}
