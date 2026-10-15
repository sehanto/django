"""Check that every organization's audit trail is complete and unchanged.

Recomputes the hash chain the database writes with each recorded change (audit migration
0010). Run daily from cron: a success line on stdout, problems on stderr with exit status 1.
With ``CHAIN_CHECKPOINT_FILE`` set, each organization's trail must still lead to its last
signed checkpoint, and a run without problems appends the current heads (apps/audit/checkpoints.py).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.audit.checkpoints import Chain, configured_log
from apps.audit.selectors import trail_head, trail_mismatch, verify_trail
from apps.core.management.organizations import selected_organizations


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
        organizations = selected_organizations(organization)
        log = configured_log()
        checkpoints, log_problems = log.latest(Chain.AUDIT_TRAIL) if log else ({}, [])
        problems, changes, heads = [str(p) for p in log_problems], 0, {}
        for org in organizations:
            problems += [f"{org.pk} {org.name}: {p}" for p in self._problems(org, expect_head, checkpoints.get(org.pk))]
            head = trail_head(org)
            if head:
                changes += head[0]
                heads[org.pk] = head
            if organization:
                self.stdout.write(f"Trail head of {org.name}: {f'{head[0]}:{head[1]}' if head else 'no changes recorded'}")
        checked = len(organizations)

        if problems:
            for line in problems:
                self.stderr.write(line)
            raise CommandError(f"Audit trail verification failed: {len(problems)} problem(s).")
        if log:
            log.append(Chain.AUDIT_TRAIL, heads)
        self.stdout.write(f"Audit trail intact: {checked} organization(s), {changes} recorded change(s).")

    @staticmethod
    def _problems(org, expect_head: tuple[int, str] | None, checkpoint):
        """What is wrong with one organization's trail: in itself, or against a head noted earlier."""
        for problem in verify_trail(org):
            yield f"change #{problem.sequence}: {problem.problem}"
        if expect_head and (mismatch := trail_mismatch(org, *expect_head)):
            yield mismatch
        if checkpoint and (mismatch := trail_mismatch(org, checkpoint.sequence, checkpoint.hash)):
            yield f"checkpoint of {checkpoint.recorded_at}: {mismatch}"
