from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiSessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api.authentication.ApiSessionAuthentication"
    name = "SessionAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": "Django same-origin session. Unsafe methods also require X-CSRFToken.",
        }
