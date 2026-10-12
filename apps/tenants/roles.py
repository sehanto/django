"""Role-based access control, kept free of Django ORM concerns."""

from enum import IntEnum

from django.db import models
from django.utils.translation import gettext_lazy as _


class Permission(IntEnum):
    VIEW = 1  # read data and reports
    WRITE = 2  # record transactions
    MANAGE = 3  # organization settings, members, plan


class Role(models.TextChoices):
    OWNER = "owner", _("Owner")
    ADMIN = "admin", _("Administrator")
    ACCOUNTANT = "accountant", _("Accountant / finance staff")
    VIEWER = "viewer", _("View only")

    def allows(self, permission: Permission) -> bool:
        return ROLE_LEVEL[self] >= permission


ROLE_LEVEL = {
    Role.OWNER: Permission.MANAGE,
    Role.ADMIN: Permission.MANAGE,
    Role.ACCOUNTANT: Permission.WRITE,
    Role.VIEWER: Permission.VIEW,
}
