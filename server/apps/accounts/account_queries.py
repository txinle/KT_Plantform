from .models import ClassGroup, User

def _class_queryset(user):
    qs = ClassGroup.objects.all()
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        qs = qs.filter(owner=user)
    return qs

def _student_queryset(user):
    qs = User.objects.filter(role=User.Role.STUDENT).select_related("class_group")
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        qs = qs.filter(class_group__owner=user)
    return qs


def resolve_home_url(user):
    from django.urls import reverse
    if user.is_superuser or user.role == User.Role.ADMIN:
        return reverse("admin:index")
    if user.role == User.Role.TEACHER:
        return reverse("courses:list")
    return reverse("assessment:dashboard")
