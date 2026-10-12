"""Small, reusable view helpers shared by every app."""

from django.contrib import messages

from .exceptions import DomainError


class DomainErrorFormMixin:
    """Turn a ``DomainError`` raised by a service into a form error.

    Subclasses implement ``perform(form)`` which calls the service layer and
    returns the redirect response.
    """

    def form_valid(self, form):
        try:
            return self.perform(form)
        except DomainError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)

    def perform(self, form):  # pragma: no cover - abstract
        raise NotImplementedError


def flash_domain_error(request, exc: DomainError) -> None:
    messages.error(request, exc.message)
