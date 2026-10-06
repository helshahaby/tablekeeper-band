class ApiError(Exception):
    """An HTTP error response with the specification's error body."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def malformed(message="Malformed request."):
    return ApiError(400, "malformed_request", message)


def invalid(message="Validation failed."):
    return ApiError(422, "validation_failed", message)


def not_found(message="Not found."):
    return ApiError(404, "not_found", message)


def unauthenticated(message="Authentication required."):
    return ApiError(401, "unauthenticated", message)
