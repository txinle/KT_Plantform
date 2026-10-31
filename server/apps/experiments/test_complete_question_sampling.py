from types import SimpleNamespace
from django.test import SimpleTestCase, TestCase
from apps.question_bank.models import Concept, QuestionConcept
from .models import Experiment, ExperimentSamplingKC
from .sampling import SamplingService, SamplingValidationError
from . import test_group_snapshots


class PreparedCompleteQuestionTests(SimpleTestCase):
    def setUp(self):
        self.definition = SimpleNamespace(sampling_mode='total_count', sampling_total_count=1,
            sampling_match_scope='any_level', cross_experiment_item_policy='unrestricted',
            review_count=0, sampling_pinned_question_ids=[])
        group = SimpleNamespace(kind='reading')
        self.questions = [SimpleNamespace(pk=index, bank_id=1, sequence=index,
            group_id=1 if index <= 3 else None, group=group if index <= 3 else None,
            group_order=index if index <= 3 else None, question_type='single_choice',
            educational_type='standard', concept_links=SimpleNamespace(all=lambda: []))
            for index in range(1, 5)]

    def service(self, **history):
        return SamplingService(self.definition, prepared_candidates=self.questions,
            prepared_rules=[], prepared_type_quotas={}, **history)

    def test_member_history_excludes_the_entire_unit(self):
        self.definition.cross_experiment_item_policy = 'exclude_previous'
        self.assertEqual([question.pk for question in self.service(completed_question_ids={3}).resolve().candidates], [4])

    def test_pins_resolve_any_member_to_all_ordered_members(self):
        self.definition.sampling_pinned_question_ids = [3]
        self.assertEqual([question.pk for question in self.service().select('01' * 32)], [1, 2, 3])

    def test_mixed_review_uses_one_review_unit(self):
        self.definition.cross_experiment_item_policy = 'mixed_review'
        self.definition.review_count = 1
        self.assertEqual([question.pk for question in self.service(completed_question_ids={3}).select('01' * 32)], [1, 2, 3])

    def test_non_group_random_selection_preserves_existing_seed_order(self):
        import random
        for question in self.questions:
            question.group_id = None
        self.definition.sampling_total_count = 2
        expected = sorted(question.pk for question in random.Random(int(('01' * 32)[:16], 16)).sample(self.questions, 2))
        self.assertEqual([question.pk for question in self.service().select('01' * 32)], expected)

    def test_words_translation_and_essay_are_distinct_sampling_types(self):
        self.questions[0].group.kind = 'words'
        self.questions[3].question_type = 'short_answer'
        self.questions[3].educational_type = 'translation'
        self.questions.append(SimpleNamespace(**{**vars(self.questions[3]), 'pk': 5, 'sequence': 5, 'educational_type': 'essay'}))
        self.definition.sampling_total_count = 3
        service = SamplingService(self.definition, prepared_candidates=self.questions,
            prepared_rules=[], prepared_type_quotas={'words': 1, 'translation': 1, 'essay': 1})
        self.assertEqual([question.pk for question in service.select('01' * 32)], [1, 2, 3, 4, 5])


class CompleteQuestionSamplingTests(TestCase):
    setUp = test_group_snapshots.GroupSnapshotTests.setUp
    publish = test_group_snapshots.GroupSnapshotTests.publish

    def experiment(self, **changes):
        return Experiment.objects.create(course=self.course, owner=self.teacher,
            created_by=self.teacher, question_bank=self.bank, name='Whole questions',
            sampling_mode='total_count', sampling_total_count=1, **changes)

    def test_type_quota_selects_every_member_as_one_question(self):
        experiment = self.experiment()
        experiment.question_quotas.create(question_type='reading', amount=1)
        selected = SamplingService(experiment).select('01' * 32)
        self.assertEqual([q.pk for q in selected], list(self.group.members.order_by('group_order').values_list('pk', flat=True)))

    def test_history_excludes_whole_group_when_one_member_is_used(self):
        experiment = self.experiment(cross_experiment_item_policy='exclude_previous')
        selected = SamplingService(experiment, completed_question_ids={self.group.members.last().pk}).select('01' * 32)
        self.assertEqual([q.group_id for q in selected], [None])

    def test_kc_match_retains_members_without_that_kc(self):
        experiment = self.experiment()
        concept = Concept.objects.create(course=self.course, code='KC2', name='Only one member')
        question = self.group.members.first()
        QuestionConcept.objects.create(question=question, concept=concept, parent_link=question.concept_links.get(is_primary=True))
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=concept, match_scope='any_level')
        self.assertEqual(len(SamplingService(experiment).select('01' * 32)), 3)

    def test_mixed_review_counts_a_group_once(self):
        experiment = self.experiment(cross_experiment_item_policy='mixed_review', review_count=1)
        selected = SamplingService(experiment, completed_question_ids={self.group.members.last().pk}).select('01' * 32)
        self.assertEqual([q.group_id for q in selected], [self.group.pk] * 3)

    def test_pinned_member_selects_whole_group_and_history_conflict_is_explicit(self):
        experiment = self.experiment(sampling_pinned_question_ids=[self.group.members.last().pk])
        self.assertEqual(len(SamplingService(experiment).select('01' * 32)), 3)
        experiment.cross_experiment_item_policy = 'exclude_previous'
        with self.assertRaises(SamplingValidationError):
            SamplingService(experiment, completed_question_ids={self.group.members.first().pk}).select('01' * 32)

    def test_preview_commit_freezes_and_selects_whole_group(self):
        from .api.views import _participant_projection, _preview_projection
        experiment, version = self.publish(sampling_mode='total_count', sampling_total_count=1,
            sampling_pinned_question_ids=[self.group.members.last().pk])
        self.assertEqual(version.question_snapshots.count(), 4)
        participant = experiment.class_tasks.get().participant_snapshots.get()
        self.assertEqual(len(participant.assignment_snapshot), 3)
        preview = experiment.publish_plans.get().preview_run
        self.assertEqual(_preview_projection(preview)['question_count'], 1)
        preview_participant = preview.class_results.get().participants.get()
        self.assertEqual(_participant_projection(preview_participant)['question_count'], 1)

    def test_frozen_selection_replays_after_source_group_changes(self):
        from .publication_services import _select_questions
        from .models import ReleaseSnapshotQuestionMapping
        experiment, _version = self.publish(sampling_mode='total_count', sampling_total_count=1,
            sampling_pinned_question_ids=[self.group.members.last().pk])
        mappings = list(ReleaseSnapshotQuestionMapping.objects.filter(release_snapshot=experiment.frozen_release_snapshot))
        before = _select_questions(experiment, mappings, '01' * 32, '02' * 32)
        self.group.kind = 'cloze'
        self.group.save(update_fields=['kind'])
        after = _select_questions(experiment, mappings, '01' * 32, '02' * 32)
        self.assertEqual([row['candidate'].source_question_id for row in before], [row['candidate'].source_question_id for row in after])
        self.assertEqual(len(after), 3)

    def test_kc_quotas_cannot_allocate_one_group_twice(self):
        experiment = self.experiment()
        experiment.sampling_mode, experiment.sampling_total_count = 'kc_quota', None
        experiment.save()
        second = Concept.objects.create(course=self.course, code='KC2', name='Another reading skill')
        question = self.group.members.last()
        QuestionConcept.objects.create(question=question, concept=second, parent_link=question.concept_links.get(is_primary=True))
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.kc, quota=1, match_scope='any_level')
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=second, quota=1, match_scope='any_level')
        result = SamplingService(experiment).select('01' * 32)
        self.assertEqual(len(result), 4)
        self.assertEqual(len({question.pk for question in result}), 4)

    def test_inventory_counts_complete_questions_and_exposes_pin_ids(self):
        from apps.api.test_support import domain_payload
        self.client.force_login(self.teacher)
        response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/experiments/sampling-inventory/',
            {'question_bank_ids': [self.bank.pk], 'kc_codes': [], 'match_scope': 'any_level'}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        data = domain_payload(response)
        self.assertEqual(data['candidate_count'], 2)
        self.assertEqual(sorted(row['item_count'] for row in data['questions']), [1, 3])

    def test_course_api_saves_group_type_quota_and_pinned_question(self):
        from apps.api.test_support import domain_payload
        self.client.force_login(self.teacher)
        pin = self.group.members.first().pk
        response = self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/experiments/', {
            'name': 'Pinned reading', 'question_bank_ids': [self.bank.pk],
            'sampling': {'mode': 'total_count', 'total_count': 1, 'match_scope': 'any_level',
                'nodes': [], 'type_quotas': [{'question_type': 'reading', 'amount': 1}], 'pinned_question_ids': [pin]},
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        data = domain_payload(response)
        self.assertEqual(data['sampling']['pinned_question_ids'], [pin])
        self.assertEqual(data['sampling']['type_quotas'], [{'question_type': 'reading', 'amount': 1}])
