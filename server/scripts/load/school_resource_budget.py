"""Measured memory evidence. Never adjusts cgroups or PostgreSQL settings."""
import math


def budget(rows, host_total, host_available):
    unique = {row['container_id']: row for row in rows}
    combined = sum(row['working_set_bytes'] for row in unique.values())
    recommended = {key: math.ceil(row['working_set_bytes'] * 1.30)
                   for key, row in unique.items() if row['scope'] == 'isolated_test'}
    checks = {'host_headroom': host_available >= 2*1024**3,
        'combined_working_set_plus_margin': math.ceil(combined * 1.30) + 2*1024**3 <= host_total,
        'candidate_margin': all(row['memory_max'] == 'max' or int(row['memory_max']) >= recommended[key]
            for key, row in unique.items() if key in recommended)}
    return {'combined_working_set_bytes': combined, 'recommended_minimum_caps': recommended,
            'host_total_bytes': host_total, 'host_available_bytes': host_available, 'checks': checks}
