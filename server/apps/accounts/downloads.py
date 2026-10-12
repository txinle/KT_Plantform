from io import BytesIO
from urllib.parse import quote
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from openpyxl import Workbook
from .permissions import role_required
from .models import User
from .account_queries import _class_queryset

@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def student_import_template(request):
    classes = list(_class_queryset(request.user).filter(is_active=True))
    workbook = Workbook()
    import_sheet = workbook.active
    import_sheet.title = "学生导入"
    import_sheet.append(["学号", "姓名", "班级"])
    import_sheet.freeze_panes = "A2"
    for column in ("A", "B", "C"):
        import_sheet.column_dimensions[column].number_format = "@"
    instructions = workbook.create_sheet("填写说明")
    instructions.append(["填写规则"])
    instructions.append(["仅支持 .xlsx；学号、姓名、班级均为必填字段。"])
    instructions.append(["班级必须与下列可用班级完整名称精确一致。"])
    instructions.append(["初始密码规则：S + 学号。"])
    instructions.append([])
    instructions.append(["填写示例（仅供参考，不会从本页导入）"])
    instructions.append(["学号", "姓名", "班级"])
    instructions.append(["20260001", "示例学生", str(classes[0]) if classes else "2026 示例班级"])
    instructions.append([])
    instructions.append(["可用班级完整名称"])
    for class_group in classes:
        instructions.append([str(class_group)])
    stream = BytesIO()
    workbook.save(stream)
    response = HttpResponse(
        stream.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    encoded_name = quote("学生批量导入模板.xlsx")
    response["Content-Disposition"] = (
        f"attachment; filename=student_import_template.xlsx; filename*=UTF-8''{encoded_name}"
    )
    return response
