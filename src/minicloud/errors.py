class ApiError(Exception):
    """An error that maps to an HTTP response with a stable error code."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Unauthorized(ApiError):
    status_code = 401
    code = "unauthorized"


class Forbidden(ApiError):
    status_code = 403
    code = "forbidden"


class NotFound(ApiError):
    status_code = 404
    code = "not_found"


class Conflict(ApiError):
    status_code = 409
    code = "conflict"


class QuotaExceeded(ApiError):
    status_code = 409
    code = "quota_exceeded"


class InsufficientCredits(ApiError):
    status_code = 402
    code = "insufficient_credits"
