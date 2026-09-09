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
