"""Domain/service-level exceptions, mapped to HTTP status by the API layer."""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for expected, user-facing service errors."""

    status_code = 400


class EmailAlreadyRegistered(ServiceError):
    status_code = 409


class InvalidCredentials(ServiceError):
    status_code = 401


class InvalidToken(ServiceError):
    status_code = 401


class NotFound(ServiceError):
    status_code = 404


class ValidationFailed(ServiceError):
    status_code = 422
