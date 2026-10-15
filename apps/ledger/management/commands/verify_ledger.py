"""Check that every organization's posted journal entries are complete and unchanged.

Recomputes the hash chain written when entries were posted (ledger migration 0006).
Run daily from cron: silent success line, problems on stderr with exit status 1.
With ``CHAIN_CHECKPOINT_FILE`` set, each organization's chain must still lead to its last
signed checkpoint, and a run without problems appends the current heads (apps/audit/checkpoints.py).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.audit.checkpoints import Chain, configured_log
from apps.ledger.selectors import ChainHead, chain_head, head_mismatch, verify_chain
from apps.core.management.organizations import selected_organizations


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
        organizations = selected_organizations(organization)
        log = configured_log()
        checkpoints, log_problems = log.latest(Chain.LEDGER) if log else ({}, [])
        problems, entries, heads = [str(p) for p in log_problems], 0, {}
        for org in organizations:
            problems += [f"{org.pk} {org.name}: {p}" for p in self._problems(org, expect_head, checkpoints.get(org.pk))]
            head = chain_head(org)
            if head:
                entries += head.sequence
                heads[org.pk] = (head.sequence, head.entry_hash)
            if organization:
                self.stdout.write(f"Chain head of {org.name}: {head or 'no posted entries'}")
        checked = len(organizations)

        if problems:
            for line in problems:
                self.stderr.write(line)
            raise CommandError(f"Ledger verification failed: {len(problems)} problem(s).")
        if log:
            log.append(Chain.LEDGER, heads)
        self.stdout.write(f"Ledger intact: {checked} organization(s), {entries} posted entr(y/ies).")

    @staticmethod
    def _problems(org, expect_head: ChainHead | None, checkpoint):
        """What is wrong with one organization's chain: in itself, or against a head recorded earlier."""
        for problem in verify_chain(org):
            yield f"#{problem.sequence} {problem.number}: {problem.problem}"
        if expect_head and (mismatch := head_mismatch(org, expect_head)):
            yield mismatch
        if checkpoint and (mismatch := head_mismatch(org, ChainHead(checkpoint.sequence, checkpoint.hash))):
            yield f"checkpoint of {checkpoint.recorded_at}: {mismatch}"
