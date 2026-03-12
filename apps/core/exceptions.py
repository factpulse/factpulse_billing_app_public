from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class ConflictError(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflict."
    default_code = "conflict"


class UnprocessableError(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Unprocessable entity."
    default_code = "validation_error"


class FactPulseUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "FactPulse API is unavailable."
    default_code = "factpulse_unavailable"


def custom_exception_handler(exc, context):
    """RFC 7807-inspired error format."""
    response = exception_handler(exc, context)

    if response is None:
        return response

    error_code = getattr(exc, "default_code", "error")
    if hasattr(exc, "get_codes"):
        codes = exc.get_codes()
        if isinstance(codes, str):
            error_code = codes

    # Build details list from DRF validation errors
    details = []
    if isinstance(response.data, dict):
        for field, errors in response.data.items():
            if field in ("detail", "non_field_errors"):
                if isinstance(errors, list):
                    for err in errors:
                        details.append(
                            {
                                "field": None if field == "non_field_errors" else field,
                                "code": getattr(err, "code", error_code),
                                "message": str(err),
                            }
                        )
                else:
                    details.append(
                        {
                            "field": None,
                            "code": error_code,
                            "message": str(errors),
                        }
                    )
            elif isinstance(errors, list):
                for err in errors:
                    details.append(
                        {
                            "field": field,
                            "code": getattr(err, "code", error_code),
                            "message": str(err),
                        }
                    )
            elif isinstance(errors, dict):
                for sub_field, sub_errors in errors.items():
                    if isinstance(sub_errors, list):
                        for err in sub_errors:
                            details.append(
                                {
                                    "field": f"{field}.{sub_field}",
                                    "code": getattr(err, "code", error_code),
                                    "message": str(err),
                                }
                            )

    # Determine the top-level message
    if len(details) == 1 and details[0]["field"] is None:
        message = details[0]["message"]
    elif details:
        message = "The payload contains validation errors."
    else:
        message = str(exc.detail) if hasattr(exc, "detail") else str(exc)

    response.data = {
        "error": {
            "code": error_code,
            "message": message,
            "details": details
            if details and not (len(details) == 1 and details[0]["field"] is None)
            else [],
        }
    }

    return response
