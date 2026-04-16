from typing import Any, Dict, Optional


class AIpalBaseException(Exception):
    """Base exception class for AIPAL application."""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary representation."""
        return {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(AIpalBaseException):
    """Raised when input validation fails."""

    def __init__(
        self,
        message: str = "Validation failed",
        field: Optional[str] = None,
        value: Optional[Any] = None,
        **kwargs,
    ) -> None:
        details = kwargs.get("details", {})
        if field:
            details["field"] = field
        if value is not None:
            details["value"] = str(value)
        super().__init__(message, details=details, **kwargs)


class NotFoundError(AIpalBaseException):
    """Raised when a requested resource is not found."""

    def __init__(
        self,
        message: str = "Resource not found",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        details = kwargs.get("details", {})
        if resource_type:
            details["resource_type"] = resource_type
        if resource_id:
            details["resource_id"] = str(resource_id)
        super().__init__(message, details=details, **kwargs)


class AuthenticationError(AIpalBaseException):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Authentication failed", **kwargs) -> None:
        super().__init__(message, **kwargs)


class AuthorizationError(AIpalBaseException):
    """Raised when authorization fails."""

    def __init__(self, message: str = "Access denied", **kwargs) -> None:
        super().__init__(message, **kwargs)


class DatabaseError(AIpalBaseException):
    """Raised when database operations fail."""

    def __init__(
        self,
        message: str = "Database operation failed",
        operation: Optional[str] = None,
        **kwargs,
    ) -> None:
        details = kwargs.get("details", {})
        if operation:
            details["operation"] = operation
        super().__init__(message, details=details, **kwargs)


class CacheError(AIpalBaseException):
    """Raised when cache operations fail."""

    def __init__(
        self,
        message: str = "Cache operation failed",
        operation: Optional[str] = None,
        **kwargs,
    ) -> None:
        details = kwargs.get("details", {})
        if operation:
            details["operation"] = operation
        super().__init__(message, details=details, **kwargs)


class BusinessLogicError(AIpalBaseException):
    """Raised when business logic validation fails."""

    def __init__(self, message: str = "Business logic error", **kwargs) -> None:
        super().__init__(message, **kwargs)


class ExternalServiceError(AIpalBaseException):
    """Raised when external service calls fail."""

    def __init__(
        self,
        message: str = "External service error",
        service_name: Optional[str] = None,
        status_code: Optional[int] = None,
        **kwargs,
    ) -> None:
        details = kwargs.get("details", {})
        if service_name:
            details["service_name"] = service_name
        if status_code:
            details["status_code"] = status_code
        super().__init__(message, details=details, **kwargs)


class ConfigurationError(AIpalBaseException):
    """Raised when configuration is invalid."""

    def __init__(
        self,
        message: str = "Configuration error",
        config_key: Optional[str] = None,
        **kwargs,
    ) -> None:
        details = kwargs.get("details", {})
        if config_key:
            details["config_key"] = config_key
        super().__init__(message, details=details, **kwargs)
