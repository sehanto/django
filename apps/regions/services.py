"""Loading the region list (the only writer of the region tables)."""

import csv
import gzip
import io
import re
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from .models import District, Province, Regency, Village

DATA_FILE = Path(__file__).resolve().parent / "data" / "wilayah.csv.gz"
LEVELS = (Province, Regency, District, Village)  # by the number of dots in the code
CODE = re.compile(r"^\d{2}(\.\d{2}(\.\d{2}(\.\d{4})?)?)?$")


@dataclass(frozen=True)
class LoadResult:
    created: int
    updated: int
    deactivated: int


def read_rows(path: Path = DATA_FILE) -> list[tuple[str, str, str]]:
    """(code, name, postcode) rows of the data file, checked: well-formed codes, every parent
    listed before its children, a 5-digit postcode on every village."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(io.StringIO(f.read()))
        header = next(reader)
        if header != ["code", "name", "postal_code"]:
            raise ValueError(f"unexpected header {header}")
        rows, seen = [], set()
        for code, name, postal_code in reader:
            name = " ".join(name.split())
            if not CODE.match(code) or not 0 < len(name) <= 100:
                raise ValueError(f"bad row {code!r}")
            if "." in code and code.rsplit(".", 1)[0] not in seen:
                raise ValueError(f"{code} comes before its parent")
            if (code.count(".") == 3) != bool(re.fullmatch(r"\d{5}", postal_code)):
                raise ValueError(f"{code}: postcode {postal_code!r}")
            seen.add(code)
            rows.append((code, name, postal_code))
    return rows


@transaction.atomic
def load_regions(rows: list[tuple[str, str, str]]) -> LoadResult:
    """Insert new codes, rename changed ones and deactivate codes no longer listed (addresses
    may still point to them). Idempotent."""
    created = updated = deactivated = 0
    for depth, model in enumerate(LEVELS):
        wanted = {code: (name, postal) for code, name, postal in rows if code.count(".") == depth}
        existing = {r.code: r for r in model.objects.all()}
        parent = {1: "province_id", 2: "regency_id", 3: "district_id"}.get(depth)
        new, changed = [], []
        for code, (name, postal) in wanted.items():
            fields = {"name": name, "is_active": True}
            if parent:
                fields[parent] = code.rsplit(".", 1)[0]
            if model is Village:
                fields["postal_code"] = postal
            row = existing.get(code)
            if row is None:
                new.append(model(code=code, **fields))
            elif any(getattr(row, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(row, k, v)
                changed.append(row)
        model.objects.bulk_create(new, batch_size=5000)
        if changed:
            model.objects.bulk_update(changed, ["name", "is_active", *([parent] if parent else []),
                                                *(["postal_code"] if model is Village else [])], batch_size=5000)
        gone = [code for code, row in existing.items() if row.is_active and code not in wanted]
        for start in range(0, len(gone), 5000):
            deactivated += model.objects.filter(code__in=gone[start:start + 5000]).update(is_active=False)
        created, updated = created + len(new), updated + len(changed)
    return LoadResult(created, updated, deactivated)
