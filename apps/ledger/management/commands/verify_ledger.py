"""Check that every organization's posted journal entries are complete and unchanged.

Recomputes the hash chain written when entries were posted (ledger migration 0006).
Run daily from cron: silent success line, problems on stderr with exit status 1.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.ledger.selectors import ChainHead, chain_head, head_mismatch, verify_chain
from apps.tenants.models import Organization


def parse_head(value: str) -> ChainHead:
    sequence, _, entry_hash = value.partition(":")
    if not sequence.isdigit() or len(entry_hash) != 64:
        raise CommandError("--expect-head must look like SEQUENCE:SHA256 (as printed by this command or the books export).")
    return ChainHead(int(sequence), entry_hash.lower())


class Command(BaseCommand):
    help = "Verify the tamper-evident hash chain of posted journal entries."

    def add_arguments(self, parser):
        parser.add_argument("--organization", type=int, help="Only this organization (ID).")
        parser.add_argument(
            "--expect-head", type=parse_head,
            help="SEQUENCE:SHA256 recorded earlier (e.g. from a books export), to detect a rewritten chain.",
        )

    def handle(self, *args, organization, expect_head, **options):
        if expect_head and not organization:
            raise CommandError("--expect-head needs --organization.")
        organizations = Organization.objects.order_by("pk")
        if organization:
            organizations = organizations.filter(pk=organization)
            if not organizations.exists():
                raise CommandError(f"Organization {organization} does not exist.")

        problems, checked, entries = [], 0, 0
        for org in organizations:
            checked += 1
            for problem in verify_chain(org):
                problems.append(f"{org.pk} {org.name}: #{problem.sequence} {problem.number}: {problem.problem}")
            if expect_head and (mismatch := head_mismatch(org, expect_head)):
                problems.append(f"{org.pk} {org.name}: {mismatch}")
            head = chain_head(org)
            entries += head.sequence if head else 0
            if organization:
                self.stdout.write(f"Chain head of {org.name}: {head or 'no posted entries'}")

        if problems:
            for line in problems:
                self.stderr.write(line)
            raise CommandError(f"Ledger verification failed: {len(problems)} problem(s).")
        self.stdout.write(f"Ledger intact: {checked} organization(s), {entries} posted entr(y/ies).")
