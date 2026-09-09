import json
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


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
