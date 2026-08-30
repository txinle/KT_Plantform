from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class FixedPageNumberPagination(PageNumberPagination):
    page_size = 20

    def get_paginated_response(self, data):
        return Response(
            {
                "items": data,
                "page": {
                    "number": self.page.number,
                    "size": self.page_size,
                    "total_items": self.page.paginator.count,
                    "total_pages": self.page.paginator.num_pages,
                    "start": self.page.start_index(),
                    "end": self.page.end_index(),
                },
            }
        )
