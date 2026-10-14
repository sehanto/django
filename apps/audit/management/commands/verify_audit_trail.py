"""Check that every organization's audit trail is complete and unchanged.

Recomputes the hash chain the database writes with each recorded change (audit migration
0010). Run daily from cron: a success line on stdout, problems on stderr with exit status 1.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.audit.models import RecordChange
from apps.audit.selectors import trail_head, verify_trail
from apps.tenants.models import Organization


def parse_head(value: str) -> tuple[int, str]:
    sequence, _, change_hash = value.partition(":")
    if not sequence.isdigit() or len(change_hash) != 64:
        raise CommandError("--expect-head must look like SEQUENCE:SHA256 (as printed by this command or the change history page).")
    return int(sequence), change_hash.lower()


class Command(BaseCommand):
    help = "Verify the tamper-evident hash chain of the audit trail (record changes)."

    def add_arguments(self, parser):
        parser.add_argument("--organization", type=int, help="Only this organization (ID).")
        parser.add_argument(
            "--expect-head", type=parse_head,
            help="SEQUENCE:SHA256 noted earlier, to detect a rewritten trail: that change must still be in the chain.",
        )

    def handle(self, *args, organization, expect_head, **options):
        if expect_head and not organization:
            raise CommandError("--expect-head needs --organization.")
        organizations = Organization.objects.order_by("pk")
        if organization:
            organizations = organizations.filter(pk=organization)
            if not organizations.exists():
                raise CommandError(f"Organization {organization} does not exist.")

        problems, checked, changes = [], 0, 0
        for org in organizations:
            checked += 1
            problems += [f"{org.pk} {org.name}: change #{p.sequence}: {p.problem}" for p in verify_trail(org)]
            if expect_head:
                sequence, change_hash = expect_head
                kept = RecordChange.objects.filter(organization=org, sequence=sequence).values_list("change_hash", flat=True).first()
                if kept != change_hash:
                    problems.append(f"{org.pk} {org.name}: change #{sequence} is no longer {change_hash} (the trail was rewritten)")
            head = trail_head(org)
            changes += head[0] if head else 0
            if organization:
                self.stdout.write(f"Trail head of {org.name}: {f'{head[0]}:{head[1]}' if head else 'no changes recorded'}")

        if problems:
            for line in problems:
                self.stderr.write(line)
            raise CommandError(f"Audit trail verification failed: {len(problems)} problem(s).")
        self.stdout.write(f"Audit trail intact: {checked} organization(s), {changes} recorded change(s).")
