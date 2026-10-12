"""Domain-level exceptions.

Services raise these instead of HTTP-aware errors so that business rules stay
independent of the delivery mechanism (web views, admin, CLI, API).
"""


class DomainError(Exception):
    """Base class for every business-rule violation."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationFailed(DomainError):
    """Input is structurally valid but violates a business rule."""


class PeriodLocked(DomainError):
    """A transaction falls inside a closed (locked) accounting period."""


class InvalidStateTransition(DomainError):
    """An action is not allowed in the document's current status."""


class PlanLimitExceeded(DomainError):
    """The organization's subscription plan does not allow the action."""
