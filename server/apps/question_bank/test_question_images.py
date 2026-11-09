import base64
import importlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings
from PIL import Image


def image_bytes(color='white'):
    output = BytesIO()
    Image.new('RGB', (24, 12), color).save(output, format='PNG')
    return output.getvalue()


class QuestionImageContentTests(SimpleTestCase):
    def setUp(self):
        try:
            self.images = importlib.import_module('apps.question_bank.services.question_images')
        except ImportError:
            self.fail('题目图片内容服务尚未实现。')
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.directory.name))
        override.enable()
        self.addCleanup(override.disable)

    def test_canonical_identity_uses_image_bytes_and_not_source_filename(self):
        first = self.images.canonical_image(image_bytes(), '电路.png')
        second = self.images.canonical_image(image_bytes(), 'another-name.png')
        self.assertEqual(first, second)
        self.assertRegex(first[0], r'^[0-9a-f]{64}\.png$')
        self.assertTrue(first[1].startswith('data:image/png;base64,'))
        self.assertNotEqual(first[0], self.images.canonical_image(image_bytes('red'), '电路.png')[0])

    def test_invalid_image_content_is_rejected(self):
        with self.assertRaises(ValueError):
            self.images.canonical_image(b'<script>alert(1)</script>', 'picture.png')

    def test_png_with_an_invalid_checksum_is_reported_as_an_invalid_image(self):
        damaged = bytearray(image_bytes())
        damaged[damaged.index(b'IDAT') + 7] ^= 1
        with self.assertRaises(ValueError):
            self.images.canonical_image(bytes(damaged), 'diagram.png')

    def test_truncated_jpeg_is_rejected_even_when_the_header_is_intact(self):
        stream = BytesIO()
        Image.new('RGB', (32, 32), 'red').save(stream, format='JPEG')
        with self.assertRaises(ValueError):
            self.images.canonical_image(stream.getvalue()[:-12], 'diagram.jpg')

    def test_projection_only_contains_images_from_authorized_returned_fields(self):
        stem_key, stem_uri = self.images.canonical_image(image_bytes(), 'stem.png')
        explanation_key, explanation_uri = self.images.canonical_image(image_bytes('red'), 'answer.png')
        staged = {stem_key: stem_uri, explanation_key: explanation_uri}
        prompt = {'text': '{{image:' + stem_key + '}}', 'options': [{'key': 'A', 'text': '1'}]}
        hidden = self.images.with_question_images(prompt, assets=staged)
        self.assertEqual(hidden['images'], {stem_key: stem_uri})
        self.assertNotIn('images', prompt)
        full = self.images.with_question_images({**prompt, 'explanation': '{{image:' + explanation_key + '}}'}, assets=staged)
        self.assertEqual(set(full['images']), {stem_key, explanation_key})

    def test_plain_question_projection_keeps_the_existing_payload(self):
        payload = {'text': '普通题干\n第二行', 'options': [{'key': 'A', 'text': '文本选项'}]}
        self.assertEqual(self.images.with_question_images(payload), payload)
        self.assertNotIn('images', self.images.with_question_images(payload))

    def test_persistence_is_immutable_idempotent_and_separate_from_preview(self):
        key, uri = self.images.canonical_image(image_bytes(), 'diagram.png')
        payload = {'text': '{{image:' + key + '}}'}
        self.images.with_question_images(payload, assets={key: uri})
        self.assertFalse((Path(self.directory.name) / 'question-images').exists())
        self.images.persist_image_assets({key: uri})
        self.images.persist_image_assets({key: uri})
        self.assertEqual(self.images.with_question_images(payload)['images'], {key: uri})
        self.assertEqual(len(list((Path(self.directory.name) / 'question-images').iterdir())), 1)

    def test_persistence_rejects_content_with_a_different_digest(self):
        key, _ = self.images.canonical_image(image_bytes(), 'diagram.png')
        wrong = 'data:image/png;base64,' + base64.b64encode(image_bytes('red')).decode()
        with self.assertRaises(ValueError):
            self.images.persist_image_assets({key: wrong})
        self.assertFalse((Path(self.directory.name) / 'question-images' / key).exists())

    def test_editor_can_reorder_existing_images_but_cannot_reference_other_assets(self):
        key, _ = self.images.canonical_image(image_bytes(), 'diagram.png')
        other, _ = self.images.canonical_image(image_bytes('red'), 'other.png')
        old = ['文字 {{image:' + key + '}}']
        self.images.validate_existing_image_references(['{{image:' + key + '}} 修改后'], old)
        with self.assertRaises(ValueError):
            self.images.validate_existing_image_references(['{{image:' + other + '}}'], old)
        with self.assertRaises(ValueError):
            self.images.validate_existing_image_references(['{{image:missing.png}}'], old)
