"""Immutable image content carried by the existing question and snapshot strings."""
import base64
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import warnings

from django.conf import settings
from PIL import Image


IMAGE_MARKER = re.compile(r'\{\{image:([0-9a-f]{64}\.(?:png|jpg|webp))\}\}')
_ANY_IMAGE_MARKER = re.compile(r'\{\{image:([^}\n]+)\}\}')
_FORMATS = {'PNG': ('png', 'image/png'), 'JPEG': ('jpg', 'image/jpeg'), 'WEBP': ('webp', 'image/webp')}


def canonical_image(data, filename):
    if not data or len(data) > 8 * 1024 * 1024:
        raise ValueError(f'图片 {filename} 必须为非空且不超过 8 MB 的文件。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                image.verify()
            if image_format in _FORMATS:
                with Image.open(BytesIO(data)) as image:
                    image.load()
    except (SyntaxError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError(f'无法读取图片 {filename}，请使用有效的 PNG、JPEG 或 WebP 文件。') from error
    if image_format not in _FORMATS:
        raise ValueError(f'图片 {filename} 请转换为 PNG、JPEG 或 WebP 格式。')
    extension, mime = _FORMATS[image_format]
    key = f'{hashlib.sha256(data).hexdigest()}.{extension}'
    return key, f'data:{mime};base64,{base64.b64encode(data).decode("ascii")}'


def image_keys(value):
    if isinstance(value, str):
        return set(IMAGE_MARKER.findall(value))
    if isinstance(value, dict):
        return set().union(*(image_keys(item) for key, item in value.items() if key != 'images'))
    if isinstance(value, (list, tuple)):
        return set().union(*(image_keys(item) for item in value))
    return set()


def persist_image_assets(assets):
    for key, uri in assets.items():
        try:
            data = base64.b64decode(uri.split(',', 1)[1], validate=True)
            actual_key, actual_uri = canonical_image(data, key)
        except (IndexError, TypeError, ValueError) as error:
            raise ValueError('图片内容校验失败，请重新上传原文件。') from error
        if actual_key != key or actual_uri != uri:
            raise ValueError('图片内容与标识不一致，请重新上传原文件。')
        directory = Path(settings.MEDIA_ROOT) / 'question-images'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / key
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError('已存图片校验失败，请联系管理员。')
            continue
        # Concurrent confirmations write identical bytes for this content hash.
        temporary = NamedTemporaryFile(dir=directory, prefix='.upload-', delete=False)
        try:
            with temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary.name, target)
        finally:
            Path(temporary.name).unlink(missing_ok=True)


def _stored_image(key):
    data = (Path(settings.MEDIA_ROOT) / 'question-images' / key).read_bytes()
    digest, extension = key.split('.')
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('题目图片校验失败，请联系管理员。')
    mime = 'image/jpeg' if extension == 'jpg' else f'image/{extension}'
    return f'data:{mime};base64,{base64.b64encode(data).decode("ascii")}'


def with_question_images(payload, *, assets=None):
    references = image_keys([payload.get(field) for field in ("text", "options", "material", "explanation")])
    if not references:
        return payload
    # Explicit staging maps never resolve arbitrary references from saved files.
    images = {key: assets[key] for key in sorted(references) if key in assets} if assets is not None else {
        key: _stored_image(key) for key in sorted(references)
    }
    return {**payload, 'images': images}


def question_image_content(question):
    options = [option.text for option in question.options.all()] if question.pk else []
    return [question.text, question.explanation, *options]


def validate_existing_image_references(values, current_values):
    allowed = image_keys(current_values)
    for value in values:
        for reference in _ANY_IMAGE_MARKER.findall(value or ''):
            if reference not in allowed:
                raise ValueError('新增图片请通过题库导入上传，编辑时可保留或调整已有图片的位置。')
