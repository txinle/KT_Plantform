from django import template

from apps.accounts.course_revision import course_revision_value


register = template.Library()


@register.simple_tag
def course_revision(course):
    return course_revision_value(course)
