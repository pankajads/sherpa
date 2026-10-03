"""Map GitHub (PyGithub) errors onto Sherpa's vendor-neutral ErrorClass."""

from __future__ import annotations

from github import GithubException, RateLimitExceededException

from sherpa.core.models import ErrorClass


def classify(exc: BaseException) -> ErrorClass:
    if isinstance(exc, RateLimitExceededException):
        return ErrorClass.THROTTLED
    if isinstance(exc, GithubException):
        status = exc.status or 0
        if status in (401, 403):
            # GitHub reports secondary rate limits as 403s.
            text = str(exc.data).lower() if exc.data else ""
            return ErrorClass.THROTTLED if "rate limit" in text else ErrorClass.ACCESS_DENIED
        if status == 404:
            return ErrorClass.NOT_FOUND
        if status == 429:
            return ErrorClass.THROTTLED
        if status >= 500:
            return ErrorClass.UNAVAILABLE
        return ErrorClass.OTHER
    if isinstance(exc, ConnectionError | TimeoutError):
        return ErrorClass.UNAVAILABLE
    if isinstance(exc, ValueError | UnicodeDecodeError):
        return ErrorClass.INVALID_CONTENT
    return ErrorClass.OTHER
