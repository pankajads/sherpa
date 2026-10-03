"""Map AWS errors onto Sherpa's vendor-neutral ErrorClass."""

from __future__ import annotations

import asyncio

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from sherpa.core.models import ErrorClass

_ACCESS = (
    "AccessDenied",
    "UnauthorizedOperation",
    "AuthorizationError",
    "NotAuthorized",
    "Forbidden",
    "InvalidClientTokenId",
    "ExpiredToken",
    "UnrecognizedClient",
)
_THROTTLED = ("Throttl", "TooManyRequests", "RequestLimitExceeded", "SlowDown", "RateExceeded")
_NOT_FOUND = ("NotFound", "NoSuch")
_UNAVAILABLE = ("ServiceUnavailable", "InternalError", "InternalFailure", "ServiceFailure")


def classify(exc: BaseException) -> ErrorClass:
    """Classify by the AWS error code when there is one, else by the exception itself."""
    code = ""
    if isinstance(exc, ClientError):
        code = str(exc.response.get("Error", {}).get("Code", ""))
    if isinstance(
        exc,
        EndpointConnectionError
        | ConnectTimeoutError
        | ReadTimeoutError
        | ConnectionError
        | TimeoutError
        | asyncio.TimeoutError,
    ):
        return ErrorClass.UNAVAILABLE
    text = code or f"{type(exc).__name__} {exc}"
    for markers, error_class in (
        (_ACCESS, ErrorClass.ACCESS_DENIED),
        (_THROTTLED, ErrorClass.THROTTLED),
        (_NOT_FOUND, ErrorClass.NOT_FOUND),
        (_UNAVAILABLE, ErrorClass.UNAVAILABLE),
    ):
        if any(m in text for m in markers):
            return error_class
    if isinstance(exc, PermissionError):
        return ErrorClass.ACCESS_DENIED
    return ErrorClass.OTHER
