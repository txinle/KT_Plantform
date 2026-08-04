from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]


class UIContractTests(TestCase):
    def test_assessment_uses_inline_feedback_instead_of_native_dialogs(self):
        source = (ROOT / "static" / "js" / "assessment.js").read_text(encoding="utf-8")
        self.assertNotIn("alert(", source)
        self.assertNotIn("confirm(", source)
        self.assertIn("operation-error", source)

    def test_base_uses_local_bootstrap_assets(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("vendor/bootstrap/css/bootstrap.min.css", source)
        self.assertIn("vendor/bootstrap/js/bootstrap.bundle.min.js", source)
        self.assertNotIn("cdn.jsdelivr.net", source)

    def test_teacher_navigation_remains_visible_on_desktop(self):
        source = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn(".teacher-nav .teacher-nav-collapse { display: block; }", source)
        self.assertIn(".teacher-nav-collapse:not(.show) { display: none; }", source)

    def test_base_defines_accessible_teacher_breadcrumb_shell(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("{% block breadcrumb_items %}", source)
        self.assertIn('aria-label="层级目录"', source)
        self.assertIn('aria-current="page"', source)

    def test_question_template_preserves_fetch_hooks_and_accessible_state(self):
        source = (ROOT / "templates" / "student" / "question.html").read_text(encoding="utf-8")
        for hook in ("question-form", "save-status", "submit-btn", "skip-btn", "countdown"):
            self.assertIn(f'id="{hook}"', source)
        self.assertIn('aria-current="step"', source)
        self.assertIn('aria-live="polite"', source)
