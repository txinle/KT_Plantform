import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))


class PreparedProtocolSmokeTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('prepared_protocol_smoke'), 'Real protocol smoke driver is missing')
        import prepared_protocol_smoke
        return prepared_protocol_smoke

    def test_navigation_material_uses_real_clock_and_distinct_uuids(self):
        before = datetime.now(timezone.utc)
        first = self.module().new_navigation_material()
        second = self.module().new_navigation_material()
        after = datetime.now(timezone.utc)
        self.assertNotEqual(UUID(first['navigation_id']), UUID(second['navigation_id']))
        self.assertNotEqual(UUID(first['client_instance_id']), UUID(second['client_instance_id']))
        self.assertTrue(before <= datetime.fromisoformat(first['client_occurred_at']) <= after)
        self.assertGreaterEqual(float(first['client_monotonic_ms']), 0)
        self.assertEqual(first['reason'], 'start')

    def test_fixed_activation_receipt_comparison_keeps_visit_and_start_identity(self):
        receipt = {'session_id': 9, 'question_url': '/student/sessions/9/questions/7/',
            'admission_execution_id': 'execution', 'runtime': {'entry_receipt': {'navigation_id': 'nav',
                'visit_id': 2, 'entered_at': '2026-10-15T00:00:00Z'}, 'remaining_seconds': 200,
                'initial_writer_lease': {'writer_lease_token': 'volatile'}}}
        stable = self.module().receipt_identity(receipt)
        receipt['runtime']['remaining_seconds'] = 190
        receipt['runtime'].pop('initial_writer_lease')
        self.assertEqual(self.module().receipt_identity(receipt), stable)
        receipt['runtime']['entry_receipt']['visit_id'] = 3
        self.assertNotEqual(self.module().receipt_identity(receipt), stable)


if __name__ == '__main__':
    unittest.main()
