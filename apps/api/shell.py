import json
import os
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.middleware.csrf import get_token
from django.shortcuts import render


HASHED_ASSET_PATTERN = re.compile(r"(?:^|/)[^/]+-[A-Za-z0-9_-]{6,}\.(?:js|css)$")


def production_asset_urls():
    manifest_path = settings.VITE_MANIFEST_PATH
    if not manifest_path.exists():
        raise ImproperlyConfigured("Vite manifest is missing. Run npm run build.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest.get("src/main.ts")
    if entry is None:
        raise ImproperlyConfigured("Vite manifest has no src/main.ts entry.")
    asset_paths = [entry["file"], *entry.get("css", [])]
    asset_root = settings.VITE_ASSET_ROOT.resolve()
    for asset_path in asset_paths:
        resolved = (asset_root / asset_path).resolve()
        if (
            not resolved.is_relative_to(asset_root)
            or not HASHED_ASSET_PATTERN.search(asset_path)
            or not resolved.is_file()
        ):
            raise ImproperlyConfigured(
                f"Vite manifest references a missing or unhashed asset: {asset_path}"
            )
    asset_prefix = f"{settings.STATIC_URL}spa-assets/"
    return {
        "scripts": [f"{asset_prefix}{entry['file']}"],
        "styles": [f"{asset_prefix}{path}" for path in entry.get("css", [])],
        "vite_client": None,
    }


def _vue_assets():
    if settings.DEBUG and settings.VITE_DEV_SERVER_URL:
        return {
            "scripts": [f"{settings.VITE_DEV_SERVER_URL}/src/main.ts"],
            "styles": [],
            "vite_client": f"{settings.VITE_DEV_SERVER_URL}/@vite/client",
        }
    return production_asset_urls()


def render_vue_shell(
    request,
    *,
    module,
    title,
    course=None,
    classic_scripts=(),
):
    if getattr(request, '_kt_standalone', False) or os.getenv('FRONTEND_INTERNAL_REDIRECT'):
        from .frontend_entry import frontend_response
        return frontend_response()
    bootstrap = {
        "module": module,
        "csrf_token": get_token(request),
        "user": {
            "id": request.user.pk,
            "role": request.user.role,
            "display_name": request.user.shown_name,
            "username": request.user.username,
            "student_number": request.user.student_number,
            "uses_initial_password": request.user.uses_initial_password,
            "initial_password_reminder_pending": request.user.initial_password_reminder_pending,
        },
        "course_id": course.pk if course is not None else None,
    }
    return render(
        request,
        "spa/vue_shell.html",
        {
            "module": module,
            "title": title,
            "bootstrap": bootstrap,
            "classic_scripts": classic_scripts,
            **_vue_assets(),
        },
    )
