from django.utils.deprecation import MiddlewareMixin


class StudentPasswordChangeMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        return None
