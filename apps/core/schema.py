from drf_spectacular.extensions import OpenApiAuthenticationExtension


class OrganizationJWTScheme(OpenApiAuthenticationExtension):
    target_class = "apps.core.authentication.OrganizationJWTAuthentication"
    name = "jwtAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
