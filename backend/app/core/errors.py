"""One error type for the whole app.

Services (the files holding business rules) raise AppError when a rule is
broken, e.g. "this code has expired". They don't need to know about HTTP.
main.py turns every AppError into a JSON response:

    {"detail": "This code has expired. Request a new one."}

This matches the shape FastAPI already uses for its own errors, so the
frontend can always read error messages from `detail`.
"""


class AppError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# Shortcuts for the common cases, so services read clearly.
def bad_request(message: str) -> AppError:
    return AppError(400, message)  # the request itself is wrong


def unauthorized(message: str = "Please sign in.") -> AppError:
    return AppError(401, message)  # not signed in


def forbidden(message: str) -> AppError:
    return AppError(403, message)  # signed in, but not allowed


def not_found(message: str) -> AppError:
    return AppError(404, message)


def conflict(message: str) -> AppError:
    return AppError(409, message)  # clashes with existing data


def too_many_requests(message: str) -> AppError:
    return AppError(429, message)
