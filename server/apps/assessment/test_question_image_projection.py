from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TransactionTestCase, override_settings

from apps.api.test_support import domain_payload
from apps.experiments.models import Experiment
from apps.experiments.student_preview import student_preview_projection
from apps.question_bank.services.question_images import canonical_image, persist_image_assets
from apps.question_bank.test_question_images import image_bytes

from .models import ExperimentSession
from .prepared_content import read_display_content
from .test_student_api import StudentApiFixtureMixin


@override_settings(STUDENT_LEARNING_ANALYTICS_ENABLED=True)
class StudentQuestionImageTests(StudentApiFixtureMixin, TransactionTestCase):
    def test_images_follow_the_frozen_question_and_feedback_policy(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory),
                ASSESSMENT_PAPER_ROOT=Path(directory) / 'papers'):
            photo, photo_uri = canonical_image(image_bytes(), 'prompt.png')
            solution, solution_uri = canonical_image(image_bytes('red'), 'solution.png')
            persist_image_assets({photo: photo_uri, solution: solution_uri})
            self.question.text = '{{image:' + photo + '}}'
            self.question.explanation = '{{image:' + solution + '}}'
            self.question.save()
            self.question.options.filter(key='B').update(text=self.question.text)
            published = [(mode, *self.publish_task(name=mode, feedback_mode=mode))
                         for mode in (Experiment.Feedback.CORRECTNESS, Experiment.Feedback.FULL)]
            self.question.text = 'Edited after publication'
            self.question.explanation = 'New explanation'
            self.question.save()
            self.question.options.filter(key='B').update(text='New option')
            for mode, experiment, task in published:
                experiment.refresh_from_db()
                preview = student_preview_projection(experiment)
                self.assertEqual(preview['units'][0]['question'].get('images'), {photo: photo_uri})
                self.start(task=task)
                session = ExperimentSession.objects.get(class_task=task, student=self.student)
                assignment = session.assignments.get()
                runtime = domain_payload(self.client.get(
                    f'/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/'))
                self.assertNotIn('images', runtime['question'])
                displayed = read_display_content(runtime['prepared_paper']['question'])['question']
                self.assertEqual(displayed['images'], {photo: photo_uri})
                self.complete_session(session)
                url = (f'/api/v1/student/courses/{self.course.pk}/learning/experiments/'
                       f'{experiment.pk}/questions/{assignment.snapshot_id}/')
                feedback = self.client.get(url)
                self.assertEqual(feedback.status_code, 200, feedback.content)
                result = domain_payload(feedback)
                expected = {photo: photo_uri}
                if mode == Experiment.Feedback.FULL:
                    expected[solution] = solution_uri
                self.assertEqual(result.get('images'), expected)
                item = domain_payload(self.client.get(f'/api/v1/student/tasks/{task.pk}/results/'))['items'][0]
                self.assertEqual(item.get('images'), expected)


class DraftPreviewImageTests(SimpleTestCase):
    def test_student_answer_cannot_resolve_an_unassigned_image_hash(self):
        from .student_results import feedback_item_images

        marker = '{{image:' + 'a' * 64 + '.png}}'
        item = {'submitted_answer': marker, 'response_value': marker}
        self.assertNotIn('images', feedback_item_images(item, 'short_answer', (), marker))

    def test_group_material_and_member_options_have_separate_images(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            key, uri = canonical_image(image_bytes(), 'material.png')
            second, option_uri = canonical_image(image_bytes('red'), 'option.png')
            persist_image_assets({key: uri, second: option_uri})
            group = SimpleNamespace(pk=1, kind='reading', title='Read', instructions='',
                material='{{image:' + key + '}}', content_language='en', word_bank=[])
            question = SimpleNamespace(pk=1, question_type='single_choice', educational_type='standard',
                title='', instructions='', text='Choose', max_score=1, group_order=1, blank_id='', group=group,
                options=SimpleNamespace(order_by=lambda *_: SimpleNamespace(
                    values=lambda *_: [{'key': 'A', 'text': '{{image:' + second + '}}'}])))
            experiment = SimpleNamespace(pk=1, name='Preview', frozen_release_snapshot_id=None)
            with patch('apps.experiments.student_preview._preview_candidates', return_value=[]), patch(
                    'apps.experiments.student_preview._select_questions', return_value=[{'candidate': question}]):
                display = student_preview_projection(experiment)['units'][0]['group']
            self.assertEqual(display.get('images'), {key: uri})
            self.assertEqual(display['members'][0].get('images'), {second: option_uri})
