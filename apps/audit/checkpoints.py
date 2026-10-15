"""Signed checkpoints of the hash chains, kept outside the database.

The posted journal entries (ledger 0006) and the audit trail (audit 0010) are hash chains the
database computes: they show that a row was changed or removed, but whoever can rewrite the
database (its owner, a superuser, a restored backup) can also recompute a whole chain, which
then verifies again. So every day ``verify_ledger`` and ``verify_audit_trail`` append each
organization's chain head to a file outside the database (``CHAIN_CHECKPOINT_FILE``, best on
separate, append-only storage), after first checking that the entry the last checkpoint names
still has its hash. Each line is signed with an HMAC under ``FIELD_ENCRYPTION_KEYS``, which the
database never holds, and signs the previous line's signature too: lines can't be forged, edited,
reordered or removed from the middle unnoticed.
"""

import fcntl
import json
import os
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.core.crypto import keyed_hash, keyed_hash_candidates

VERSION = "v1"


class Chain(StrEnum):
    LEDGER = "ledger"
    AUDIT_TRAIL = "audit_trail"


@dataclass(frozen=True)
class Checkpoint:
    chain: str
    organization: int
    sequence: int
    hash: str
    recorded_at: str
    previous: str  # the signature of the line before ("" for the first)
    signature: str = ""

    def payload(self) -> str:
        return "|".join([VERSION, self.chain, str(self.organization), str(self.sequence), self.hash, self.recorded_at, self.previous])


@dataclass(frozen=True)
class LogProblem:
    line: int
    problem: str

    def __str__(self):
        return f"checkpoint file line {self.line}: {self.problem}"


def _signature(line: str) -> str:
    try:
        return json.loads(line).get("signature", "")
    except (AttributeError, ValueError):
        return ""  # a broken last line: reading the file reports it


def configured_log() -> "CheckpointLog | None":
    path = settings.CHAIN_CHECKPOINT_FILE
    return CheckpointLog(Path(path)) if path else None


class CheckpointLog:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> tuple[list[Checkpoint], list[LogProblem]]:
        """Every line, and what is wrong with the file (an unsigned, edited or out-of-order line)."""
        if not self.path.exists():
            return [], []
        with self.path.open(encoding="utf-8") as file:
            return self._parse(file.read())

    @staticmethod
    def _parse(text: str) -> tuple[list[Checkpoint], list[LogProblem]]:
        checkpoints, problems, previous = [], [], ""
        for number, line in enumerate(text.splitlines(), start=1):
            try:
                checkpoint = Checkpoint(**json.loads(line))
            except (TypeError, ValueError):
                problems.append(LogProblem(number, "not a checkpoint"))
                continue
            if checkpoint.signature not in keyed_hash_candidates(checkpoint.payload()):
                problems.append(LogProblem(number, "the signature doesn't match: the line was edited or forged"))
            elif checkpoint.previous != previous:
                problems.append(LogProblem(number, "it doesn't follow the line before: lines were removed or reordered"))
            checkpoints.append(checkpoint)
            previous = checkpoint.signature
        return checkpoints, problems

    def latest(self, chain: Chain) -> tuple[dict[int, Checkpoint], list[LogProblem]]:
        """Each organization's last checkpoint of ``chain``."""
        checkpoints, problems = self.read()
        return {c.organization: c for c in checkpoints if c.chain == chain}, problems

    def append(self, chain: Chain, heads: dict[int, tuple[int, str]]) -> None:
        """Record ``{organization id: (sequence, hash)}``, under a lock (both commands write here)."""
        if not heads:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            lines = file.read().splitlines()
            previous = _signature(lines[-1]) if lines else ""
            recorded_at = timezone.now().isoformat(timespec="seconds")
            for organization, (sequence, head_hash) in sorted(heads.items()):
                checkpoint = Checkpoint(chain, organization, sequence, head_hash, recorded_at, previous)
                checkpoint = Checkpoint(**{**asdict(checkpoint), "signature": keyed_hash(checkpoint.payload())})
                file.write(json.dumps(asdict(checkpoint), sort_keys=True) + "\n")
                previous = checkpoint.signature
            file.flush()
            os.fsync(file.fileno())
