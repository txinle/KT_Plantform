from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "交互式创建完整管理员，不覆盖已有账号，也不显示或保存明文密码。"

    def handle(self, *args, **options):
        username = input("新管理员账号: ").strip()
        try:
            username = User._meta.get_field(User.USERNAME_FIELD).clean(username, None)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages))
        if User.objects.filter(username=username).exists():
            raise CommandError("账号已存在，未修改原账号。")
        password = getpass("新密码: ")
        if password != getpass("再次输入密码: "):
            raise CommandError("两次密码不一致，未创建账号。")
        try:
            validate_password(password, User(username=username))
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages))
        User.objects.create_superuser(
            username=username, email="", password=password, role=User.Role.ADMIN
        )
        self.stdout.write(self.style.SUCCESS(f"管理员已创建：{username}"))
