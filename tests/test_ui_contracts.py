import re
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]


class UIContractTests(TestCase):
    def test_shared_theme_is_loaded_before_django_surface_styles(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn("css/kt-theme.css", source)
        self.assertLess(source.index("css/kt-theme.css"), source.index("css/app.css"))

    def test_shared_theme_defines_approved_tokens(self):
        source = (ROOT / "static" / "css" / "kt-theme.css").read_text(
            encoding="utf-8"
        )

        for token in (
            "--kt-brand-900: #0c3266",
            "--kt-action-600: #145bea",
            "--kt-page: #f6f8fb",
            "--kt-radius-surface: 0.75rem",
            "--kt-font-sans:",
        ):
            self.assertIn(token, source)

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

    def test_django_header_uses_light_single_line_brand_shell(self):
        source = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

        self.assertIn('class="brand-mark"', source)
        self.assertIn('class="teacher-nav primary-navigation"', source)
        self.assertNotIn("navbar-dark", source)
        self.assertIn('aria-label="教师工作区"', source)

    def test_teacher_navigation_remains_visible_on_desktop(self):
        source = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
        self.assertIn(".teacher-nav .teacher-nav-collapse { display: block; }", source)
        self.assertIn(".teacher-nav-collapse:not(.show) { display: none; }", source)

    def test_django_surfaces_use_approved_shape_and_control_tokens(self):
        source = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

        self.assertIn("border-radius: var(--kt-radius-surface)", source)
        self.assertIn("min-height: 2.5rem", source)
        self.assertIn("font-variant-numeric: tabular-nums", source)
        self.assertIn("@media (prefers-reduced-motion: reduce)", source)

    def test_high_value_pages_use_shared_presentation_wrappers(self):
        detail = (ROOT / "templates" / "teacher" / "course_experiment_detail.html").read_text(encoding="utf-8")
        form = (ROOT / "templates" / "teacher" / "course_experiment_form.html").read_text(encoding="utf-8")
        publish = (ROOT / "templates" / "teacher" / "course_experiment_publish_wizard.html").read_text(encoding="utf-8")
        results = (ROOT / "templates" / "teacher" / "course_experiment_results.html").read_text(encoding="utf-8")
        login = (ROOT / "templates" / "registration" / "login.html").read_text(encoding="utf-8")

        self.assertIn('class="experiment-detail-grid"', detail)
        self.assertIn('class="page-header"', form)
        self.assertIn('class="form-section"', form)
        self.assertIn('class="lifecycle workflow-steps', publish)
        self.assertIn('class="table-shell"', results)
        self.assertIn('class="login-form-panel surface-elevated"', login)

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

    def test_question_type_templates_render_fixed_judgement_controls_and_single_line_fill_blank_input(self):
        source = (ROOT / "templates" / "student" / "question.html").read_text(encoding="utf-8")

        self.assertIn("snapshot.question_type == 'true_false'", source)
        self.assertIn('value="true"', source)
        self.assertIn('value="false"', source)
        self.assertIn("snapshot.question_type == 'fill_blank'", source)
        self.assertIn('type="text"', source)
        self.assertIn("填空答案", source)

    def test_assessment_payload_restore_and_feedback_handle_judgement_answers(self):
        source = (ROOT / "static" / "js" / "assessment.js").read_text(encoding="utf-8")

        self.assertIn("type === 'true_false'", source)
        self.assertIn("formatCorrectAnswer", source)
        self.assertIn("正确", source)
        self.assertIn("错误", source)

    def test_question_edit_templates_expose_fill_blank_accepted_answers(self):
        for filename in ("question_edit.html", "course_question_edit.html"):
            with self.subTest(template=filename):
                source = (ROOT / "templates" / "teacher" / filename).read_text(encoding="utf-8")
                self.assertIn('data-question-field-group="short_answer fill_blank"', source)
                self.assertIn("form.accepted_answers_text", source)

    def test_experiment_templates_and_native_script_expose_quota_configuration(self):
        script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-selection-mode-controller", script)
        self.assertIn("data-quota-input", script)
        self.assertIn("data-quota-total", script)
        for filename in ("experiment_form.html", "course_experiment_form.html"):
            with self.subTest(template=filename):
                source = (ROOT / "templates" / "teacher" / filename).read_text(encoding="utf-8")
                self.assertIn("form.selection_mode", source)
                self.assertIn("form.quota_single_choice", source)
                self.assertIn("form.quota_true_false", source)
                self.assertIn("data-quota-total", source)

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

    def test_course_question_kc_picker_is_progressive_and_keyboard_accessible(self):
        template = (ROOT / "templates" / "teacher" / "course_question_edit.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        stylesheet = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

        for token in (
            "data-concept-picker",
            "data-search-url",
            "form.primary_concept",
            "form.secondary_concepts",
            "无 JavaScript 时仍可直接使用",
        ):
            self.assertIn(token, template)
        for token in (
            "data-concept-select",
            "role', 'combobox",
            "aria-autocomplete",
            "aria-activedescendant",
            "ArrowDown",
            "ArrowUp",
            "Enter",
            "Escape",
            "AbortController",
            "fetch(",
            "concept-tag-remove",
        ):
            self.assertIn(token, script)
        self.assertIn(".concept-combobox", stylesheet)
        self.assertIn(".concept-tags", stylesheet)

    def test_long_kc_and_page_header_text_wrap_without_root_overflow(self):
        stylesheet = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")

        self.assertRegex(
            stylesheet,
            r"\.concept-tag\s*\{[^}]*max-width:\s*100%;[^}]*min-width:\s*0;",
        )
        self.assertRegex(
            stylesheet,
            r"\.concept-tag\s*>\s*span\s*\{[^}]*min-width:\s*0;[^}]*overflow-wrap:\s*anywhere;",
        )
        self.assertRegex(
            stylesheet,
            r"\.concept-result\s*\{[^}]*overflow-wrap:\s*anywhere;",
        )
        self.assertRegex(
            stylesheet,
            r"\.page-kicker,[^}]*\.page-title,[^}]*\.page-subtitle\s*\{[^}]*overflow-wrap:\s*anywhere;",
        )

    def test_course_kc_delete_dialogs_are_not_nested_inside_table_bodies(self):
        template = (ROOT / "templates" / "teacher" / "course_bank_list.html").read_text(encoding="utf-8")
        kc_section = template.split('id="course-kc-title"', 1)[1]
        table_body = kc_section.split("<tbody>", 1)[1].split("</tbody>", 1)[0]

        self.assertNotIn('class="modal fade"', table_body)
        self.assertIn('class="modal fade"', kc_section.split("</tbody>", 1)[1])
