from django.test import SimpleTestCase


class SchoolConfidenceFixtureTests(SimpleTestCase):
    def test_confidence_profile_disables_tools_and_preserves_existing_scale(self):
        from apps.experiments.management.commands.seed_assessment_load import research_profile
        from apps.experiments.models import Experiment
        value = research_profile([], capture_protocol=True, confidence_only=True)
        self.assertEqual(value['research_tools'], {})
        self.assertEqual(value['confidence_mode'], Experiment.ConfidenceMode.ALL)
        self.assertEqual(value['confidence_scale_version'], Experiment._meta.get_field('confidence_scale_version').default)
        self.assertEqual(value['confidence_prompt_version'], Experiment._meta.get_field('confidence_prompt_version').default)

    def test_existing_protocol_profile_remains_unchanged(self):
        from apps.experiments.management.commands.seed_assessment_load import protocol_tools, research_profile
        questions = [{'question_type': 'single_choice', 'code': 'Q1'}]
        self.assertEqual(research_profile(questions, capture_protocol=True, confidence_only=False),
                         {'research_tools': protocol_tools(questions)})
