from django.apps import apps
from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from .models import User


class AdminLocalizationTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin-localization", password="password123", role=User.Role.ADMIN
        )
        self.client.force_login(self.admin_user)

    def test_application_labels_and_admin_titles_are_chinese(self):
        self.assertEqual(apps.get_app_config("accounts").verbose_name, "课程与账号")
        self.assertEqual(apps.get_app_config("question_bank").verbose_name, "题库与知识点")
        self.assertEqual(apps.get_app_config("experiments").verbose_name, "实验管理")
        self.assertEqual(apps.get_app_config("assessment").verbose_name, "作答与交互")
        self.assertEqual(apps.get_app_config("audit").verbose_name, "审计日志")
        self.assertEqual(admin.site.site_header, "KT Data Lab 管理后台")
        self.assertEqual(admin.site.index_title, "平台数据管理")

    def test_admin_index_and_user_list_present_chinese_management_copy(self):
        index = self.client.get(reverse("admin:index"))
        for label in ("课程与账号", "题库与知识点", "实验管理", "作答与交互", "审计日志"):
            self.assertContains(index, label)
        for label in ("模型名称", "新增入口", "查看或修改入口"):
            self.assertContains(index, label)
        self.assertNotContains(index, "Model name")
        self.assertContains(
            index, "<title>平台数据管理 | KT Data Lab 管理后台</title>", html=True
        )

        users = self.client.get(reverse("admin:accounts_user_changelist"))
        self.assertContains(users, "用户账号")
        self.assertContains(users, "用户名")
        self.assertContains(users, "姓名")
