import uuid


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid.uuid4().hex
        response = self.get_response(request)
        if request.path.startswith("/api/v1/"):
            response["X-Request-ID"] = request.request_id
        return response
