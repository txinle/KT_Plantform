#!/usr/bin/env python3
"""Combine completed HTTP results with server observations without repeating load."""
import argparse
import json
from pathlib import Path

from school_mixed_300 import evaluate


def baseline_checks(phase, observed):
    return {'baseline_observation_covers_auth_and_workload': observed.get('started_at', float('inf')) <= phase.get('warmup_started_at', 0)
                and observed.get('ended_at', 0) >= phase.get('ended_at', float('inf')),
        'baseline_zero_oom': observed.get('source') == 'docker_events' and observed.get('oom_kills') == 0,
        'baseline_no_swap_growth': observed.get('swap_start_bytes') is not None and observed.get('swap_end_bytes') is not None
                and observed['swap_end_bytes'] <= observed['swap_start_bytes'],
        'baseline_server_samples': bool(observed.get('memory_cpu_samples')),
        'baseline_backlog_drained': bool(observed.get('backlog_samples'))
                and all(value == 0 for value in observed['backlog_samples'][-1]['counts'].values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--mixed", required=True)
    parser.add_argument("--observation", required=True)
    parser.add_argument("--baseline-observation", required=True)
    parser.add_argument("--source-config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    values = [json.loads(Path(path).read_text(encoding="utf-8"))
              for path in (args.baseline, args.mixed, args.observation)]
    result = evaluate(*values)
    observed_baseline = json.loads(Path(args.baseline_observation).read_text(encoding='utf-8'))
    result['checks'].update(baseline_checks(values[0], observed_baseline))
    result['checks']['mixed_observation_covers_auth_and_workload'] = values[2].get('started_at', float('inf')) <= values[1].get('warmup_started_at', 0)
    result['passed'] = all(result['checks'].values())
    result['source_config'] = json.loads(Path(args.source_config).read_text(encoding='utf-8'))
    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
