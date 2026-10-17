"""Publication-time encrypted objects, served directly by the static tier."""
import base64
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from .prepared_models import PreparedContentObject


def canonical_json(document):
    return json.dumps(document, cls=DjangoJSONEncoder, sort_keys=True,
                      separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def content_root():
    return Path(getattr(settings, 'ASSESSMENT_PAPER_ROOT', settings.MEDIA_ROOT / 'assessment-papers')).resolve()


def _encrypted_body(plaintext, key, nonce):
    encrypted = AESGCM(key).encrypt(nonce, gzip.compress(plaintext, mtime=0), None)
    return canonical_json({'version': 1, 'nonce': base64.b64encode(nonce).decode(),
                           'ciphertext': base64.b64encode(encrypted).decode(), 'compression': 'gzip'})


def _persist_verified_body(body, digest):
    root = content_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root, prefix='publish-', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(body)
        stream.flush()
        # Nginx reads ciphertext as a different user from the publishing worker.
        temporary.chmod(0o644)
        os.fsync(stream.fileno())
    destination = root / f'{digest}.json'
    os.replace(temporary, destination)
    saved = destination.read_bytes()
    if saved != body or hashlib.sha256(saved).hexdigest() != digest:
        raise OSError('prepared_content_write_verification_failed')


def persist_display_content(document):
    """Deduplicate student-visible projections, never raw bank/scoring objects."""
    plaintext = canonical_json(document)
    projection_hash = hashlib.sha256(plaintext).hexdigest()
    existing = PreparedContentObject.objects.filter(pk=projection_hash).first()
    if existing:
        descriptor = existing.descriptor
        path = content_root() / f"{descriptor['sha256']}.json"
        body = path.read_bytes() if path.is_file() else None
        if body is None or hashlib.sha256(body).hexdigest() != descriptor['sha256']:
            if not descriptor.get('nonce'):
                raise OSError('prepared_content_repair_material_missing')
            restored = _encrypted_body(plaintext, base64.b64decode(descriptor['key']),
                                       base64.b64decode(descriptor['nonce']))
            if hashlib.sha256(restored).hexdigest() != descriptor['sha256']:
                raise OSError('prepared_content_repair_hash_mismatch')
            _persist_verified_body(restored, descriptor['sha256'])
        elif not descriptor.get('nonce'):
            descriptor = {**descriptor, 'nonce': json.loads(body)['nonce']}
            existing.descriptor = descriptor
            existing.save(update_fields=['descriptor'])
        path.chmod(0o644)
        return descriptor
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    body = _encrypted_body(plaintext, key, nonce)
    digest = hashlib.sha256(body).hexdigest()
    _persist_verified_body(body, digest)
    descriptor = {'url': f'/assessment-content/{digest}.json', 'sha256': digest,
                  'byte_length': len(body), 'key': base64.b64encode(key).decode(),
                  'nonce': base64.b64encode(nonce).decode()}
    record, _ = PreparedContentObject.objects.get_or_create(
        pk=projection_hash, defaults={'descriptor': descriptor})
    return record.descriptor


def read_display_content(descriptor):
    """Local verification/repair only; activation never opens these files."""
    path = content_root() / f"{descriptor['sha256']}.json"
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != descriptor['sha256']:
        raise ValueError('prepared_content_corrupt')
    envelope = json.loads(body)
    packed = AESGCM(base64.b64decode(descriptor['key'])).decrypt(
        base64.b64decode(envelope['nonce']), base64.b64decode(envelope['ciphertext']), None)
    return json.loads(gzip.decompress(packed))
