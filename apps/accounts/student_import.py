from dataclasses import dataclass
from io import BytesIO

from django.db.models import Q
from openpyxl import load_workbook

from .models import User


EXPECTED_HEADERS = ("学号", "姓名", "班级")


class StudentImportError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedStudent:
    student_number: str
    display_name: str
    class_id: int
    class_name: str


def parse_student_workbook(uploaded_file, classes):
    if not uploaded_file.name.lower().endswith(".xlsx"):
        raise StudentImportError("仅支持 .xlsx 文件")
    try:
        workbook = load_workbook(BytesIO(uploaded_file.read()), read_only=True, data_only=True)
    except Exception as exc:
        raise StudentImportError("无法读取 Excel 文件，请使用下载的参考模板") from exc

    rows = workbook.active.iter_rows(values_only=True)
    header_row = next(rows, None)
    headers = [str(value or "").strip() for value in (header_row or ())]
    if set(headers) != set(EXPECTED_HEADERS) or len(headers) != len(EXPECTED_HEADERS):
        raise StudentImportError("表头必须且只能包含：学号、姓名、班级")

    class_map = {str(class_group): class_group for class_group in classes}
    parsed = []
    seen_numbers = set()
    for row_number, values in enumerate(rows, start=2):
        record = dict(zip(headers, values))
        if all(value is None or str(value).strip() == "" for value in record.values()):
            continue
        student_number = str(record["学号"] or "").strip()
        display_name = str(record["姓名"] or "").strip()
        class_name = str(record["班级"] or "").strip()
        if not student_number or not display_name or not class_name:
            raise StudentImportError(f"第 {row_number} 行存在空字段")
        if len(student_number) > 64:
            raise StudentImportError(f"第 {row_number} 行学号不能超过 64 个字符")
        if len(display_name) > 80:
            raise StudentImportError(f"第 {row_number} 行姓名不能超过 80 个字符")
        if student_number in seen_numbers:
            raise StudentImportError(f"第 {row_number} 行学号重复：{student_number}")
        class_group = class_map.get(class_name)
        if class_group is None:
            raise StudentImportError(f"第 {row_number} 行班级无法匹配：{class_name}")
        seen_numbers.add(student_number)
        parsed.append(ParsedStudent(student_number, display_name, class_group.pk, class_name))
    if not parsed:
        raise StudentImportError("文件中没有可导入的学生")
    numbers = [item.student_number for item in parsed]
    existing_rows = User.objects.filter(
        Q(student_number__in=numbers) | Q(username__in=numbers)
    ).values_list("student_number", "username")
    existing_numbers = {
        value
        for student_number, username in existing_rows
        for value in (student_number, username)
        if value in seen_numbers
    }
    for student_number in numbers:
        if student_number in existing_numbers:
            raise StudentImportError(f"学号已存在：{student_number}")
    return parsed
