import re
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

    def test_table_scroll_shell_establishes_containing_block_to_prevent_absolute_accessibility_text_from_expanding_root(self):
        source = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        rule = re.search(r"(?m)^\.table-shell\s*\{(?P<body>[^}]*)\}", source)

        self.assertIsNotNone(rule)
        self.assertRegex(rule.group("body"), r"(?:^|;)\s*position:\s*relative\s*;")

    def test_base_defines_accessible_teacher_breadcrumb_shell(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("{% block breadcrumb_items %}", source)
        self.assertIn('aria-label="层级目录"', source)
        self.assertIn('aria-current="page"', source)

    def test_account_identity_is_not_hidden_on_mobile(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('class="account-label"', source)
        self.assertNotIn('account-label d-none', source)

    def test_every_teacher_subpage_declares_breadcrumb_items(self):
        template_root = ROOT / "templates" / "teacher"
        for path in template_root.glob("*.html"):
            if path.name == "dashboard.html":
                continue
            with self.subTest(template=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("{% block breadcrumb_items %}", source)
                self.assertIn('aria-current="page"', source)
                self.assertNotIn('class="back-link"', source)

    def test_teacher_dashboard_groups_all_six_workflow_destinations(self):
        source = (ROOT / "templates" / "teacher" / "dashboard.html").read_text(encoding="utf-8")
        for label in (
            "内容准备",
            "参与者管理",
            "实验采集",
            "管理题库",
            "管理 KC",
            "管理班级",
            "管理学生",
            "配置实验",
            "查看采集结果",
        ):
            self.assertIn(label, source)
        self.assertIn("{% url 'accounts:class_list' %}", source)
        self.assertIn("{% url 'accounts:student_list' %}", source)

    def test_question_template_preserves_fetch_hooks_and_accessible_state(self):
        source = (ROOT / "templates" / "student" / "question.html").read_text(encoding="utf-8")
        for hook in ("question-form", "save-status", "submit-btn", "skip-btn", "countdown"):
            self.assertIn(f'id="{hook}"', source)
        self.assertIn('aria-current="step"', source)
        self.assertIn('aria-live="polite"', source)

    def test_question_bank_list_visibility_contract_uses_progressive_enhancement(self):
        template = (ROOT / "templates" / "teacher" / "bank_list.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

        for token in ("<details", "<summary", "<noscript>", "data-visibility-control", "data-auto-submit"):
            self.assertIn(token, template)
        self.assertIn("data-visibility-control", script)
        self.assertIn("data-auto-submit", script)
        self.assertRegex(
            script,
            r"if \(control\.open\) control\.querySelector\('\[data-auto-submit\]'\)\?\.focus\(\);",
        )
        self.assertIn("form.setAttribute('aria-busy', 'true')", script)
        self.assertIn("form.requestSubmit()", script)
        self.assertNotIn("control.disabled", script)
        self.assertNotRegex(script, r"\[data-auto-submit\][\s\S]{0,300}\.disabled\s*=")

    def test_question_bank_list_links_remain_identifiable_without_hover(self):
        source = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

        for selector in (".cell-title", ".count-link"):
            self.assertRegex(
                source,
                rf"{re.escape(selector)}\s*\{{[^}}]*text-decoration:\s*underline;",
            )

    def test_question_bank_list_owned_row_uses_annotated_question_count(self):
        source = (ROOT / "templates" / "teacher" / "bank_list.html").read_text(encoding="utf-8")
        owned_row = source.split("{% for bank in banks %}", 1)[1].split("{% empty %}", 1)[0]

        self.assertGreaterEqual(owned_row.count("bank.question_count"), 2)
        self.assertNotIn("bank.questions.count", owned_row)
