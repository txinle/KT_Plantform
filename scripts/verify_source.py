from pathlib import Path
import ast
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
required = [
    "manage.py", "config/settings.py", "apps/accounts/models.py",
    "apps/question_bank/models.py", "apps/experiments/models.py",
    "apps/assessment/models.py", "templates/student/question.html",
    "docker-compose.yml", "README.md",
]
missing = [name for name in required if not (ROOT / name).exists()]
if missing:
    raise SystemExit(f"Missing required files: {missing}")

for path in ROOT.rglob("*.py"):
    if any(part in {".venv", ".git", "__pycache__"} for part in path.parts):
        continue
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

for path in (ROOT / "templates").rglob("*.html"):
    text = path.read_text(encoding="utf-8")
    for opening, closing in [("{% if", "{% endif %}"), ("{% for", "{% endfor %}"), ("{% block", "{% endblock %}")]:
        if text.count(opening) != text.count(closing):
            raise SystemExit(f"Template tag imbalance in {path}: {opening}/{closing}")

subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests")], check=True, cwd=ROOT)
print("Source verification passed")
