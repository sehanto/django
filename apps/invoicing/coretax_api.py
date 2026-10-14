"""Sending Coretax import files to Coretax DJP through a tax application provider (PJAP).

DJP doesn't offer a public Coretax API: taxpayers connect through a licensed PJAP, each with
its own API. The app therefore talks to a small adapter contract and ships two adapters:

* ``fake``: an in-process stand-in for development and tests; it accepts every file and gives
  each faktur and bukti potong a number.
* ``http``: a generic JSON adapter for a PJAP (or an in-house bridge) exposing
  ``POST {url}/submissions`` with ``{"document_type", "tin", "file_name", "xml"}`` (the XML
  base64-encoded) answering ``{"id", "status"}``, and ``GET {url}/submissions/{id}`` answering
  ``{"status", "items": [{"reference", "number", "error"}]}``, with ``Authorization: Bearer``.
  Adapt ``HttpConnector`` to your provider's actual API.

The files sent are exactly the ones offered for download (``coretax.py``, ``taxes/ebupot.py``);
a faktur's ``reference`` is the invoice number (RefDesc), which maps the numbers Coretax issues
back to the invoices. eBupot records carry no reference of their own: there ``reference`` is the
record's 1-based position in the file.
"""

import base64
import itertools
import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

import requests
from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.exceptions import DomainError

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 30


class ConnectorName(models.TextChoices):
    FAKE = "fake", gettext_lazy("Simulated (development)")
    HTTP = "http", gettext_lazy("PJAP / bridge (HTTP API)")


class DocumentType(models.TextChoices):
    TAX_INVOICES = "faktur_pk", gettext_lazy("Faktur Pajak Keluaran")
    INPUT_RETURNS = "retur_pm", gettext_lazy("Nota retur (Retur Pajak Masukan)")
    # eBupot (bukti potong), DJP's converter templates (bp*.zip samples, "Excel to XML" schemas).
    BPMP = "bpmp", gettext_lazy("BPMP: monthly PPh 21 of permanent employees")
    BP21 = "bp21", gettext_lazy("BP21: PPh 21 other than permanent employees")
    BP26 = "bp26", gettext_lazy("BP26: PPh 26")
    BPA1 = "bpa1", gettext_lazy("BPA1: annual PPh 21 (1721-A1)")
    BPPU = "bppu", gettext_lazy("BPPU: PPh 23 and 4(2) (unified)")
    BPNR = "bpnr", gettext_lazy("BPNR: PPh 26 on payments abroad")
    BPSP = "bpsp", gettext_lazy("BPSP: final PPh deposited yourself")


# The eBupot files: one record per bukti potong, under the root's list element.
WITHHOLDING_TYPES = (
    DocumentType.BPMP, DocumentType.BP21, DocumentType.BP26, DocumentType.BPA1, DocumentType.BPPU, DocumentType.BPNR, DocumentType.BPSP,
)


class SubmissionStatus(models.TextChoices):
    PENDING = "pending", gettext_lazy("Being processed")
    ACCEPTED = "accepted", gettext_lazy("Accepted")
    PARTIAL = "partial", gettext_lazy("Partly accepted")
    REJECTED = "rejected", gettext_lazy("Rejected")


class CoretaxError(DomainError):
    """The provider could not be reached or refused the request."""


@dataclass(frozen=True)
class Receipt:
    reference: str
    status: str


@dataclass(frozen=True)
class ItemResult:
    reference: str  # our document number (RefDesc)
    number: str = ""  # the Faktur Pajak number Coretax issued
    error: str = ""


@dataclass(frozen=True)
class Result:
    status: str
    items: list[ItemResult] = field(default_factory=list)


class CoretaxConnector(Protocol):
    def submit(self, *, document_type: str, tin: str, file_name: str, xml: bytes) -> Receipt: ...

    def fetch(self, reference: str) -> Result: ...


class FakeConnector:
    """Accepts every file at once; fakturs get consecutive numbers (code + 15 digits)."""

    _numbers = itertools.count(1)

    def submit(self, *, document_type: str, tin: str, file_name: str, xml: bytes) -> Receipt:
        from xml.etree import ElementTree as ET

        root = ET.fromstring(xml)
        items = []
        for faktur in root.iter("TaxInvoice"):
            number = f"{faktur.findtext('TrxCode', '04')}00{next(self._numbers):013d}"
            items.append({"reference": faktur.findtext("RefDesc", ""), "number": number, "error": ""})
        if document_type in WITHHOLDING_TYPES:
            records = list(root[1]) if len(root) > 1 else []  # TIN, then the list of records
            items = [
                {"reference": str(position), "number": f"{document_type.upper()[:4]}{next(self._numbers):08d}", "error": ""}
                for position, _record in enumerate(records, start=1)
            ]
        reference = f"fake-ct-{uuid.uuid4().hex[:16]}"
        cache.set(f"fake-coretax:{reference}", {"status": SubmissionStatus.ACCEPTED, "items": items}, None)
        return Receipt(reference, SubmissionStatus.PENDING)

    def fetch(self, reference: str) -> Result:
        state = cache.get(f"fake-coretax:{reference}") or {"status": SubmissionStatus.REJECTED, "items": []}
        return Result(state["status"], [ItemResult(**item) for item in state["items"]])


class RemoteStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    REJECTED = "rejected"


class HttpConnector:
    def __init__(self, *, url: str, token: str, http=None):
        if not url.startswith("https://") or not token:
            raise CoretaxError(_("The Coretax connection needs an HTTPS address and an access token."))
        self._url, self._token, self._http = url.rstrip("/"), token, http or requests.Session()

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(
                method, self._url + path, headers={"Authorization": f"Bearer {self._token}"}, timeout=TIMEOUT_SECONDS, **kwargs,
            )
        except requests.RequestException as exc:
            logger.warning("Coretax provider %s %s failed: %s", method, path, exc)
            raise CoretaxError(_("The Coretax provider is not reachable right now. Please try again.")) from exc
        if response.status_code >= 400:
            logger.error("Coretax provider %s %s returned %s: %s", method, path, response.status_code, response.text[:500])
            raise CoretaxError(_("The Coretax provider refused the request (HTTP %(status)s).") % {"status": response.status_code})
        data = response.json()
        if not isinstance(data, dict):
            raise CoretaxError(_("The Coretax provider sent an unexpected answer."))
        return data

    @staticmethod
    def _status(value) -> str:
        try:
            return RemoteStatus(str(value).lower()).value
        except ValueError:
            raise CoretaxError(_("The Coretax provider sent an unknown status.")) from None

    def submit(self, *, document_type: str, tin: str, file_name: str, xml: bytes) -> Receipt:
        data = self._call("POST", "/submissions", json={
            "document_type": document_type, "tin": tin, "file_name": file_name, "xml": base64.b64encode(xml).decode(),
        })
        reference = str(data.get("id", ""))[:64]
        if not reference:
            raise CoretaxError(_("The Coretax provider sent an unexpected answer."))
        return Receipt(reference, self._status(data.get("status", RemoteStatus.PENDING)))

    def fetch(self, reference: str) -> Result:
        data = self._call("GET", f"/submissions/{reference}")
        items = data.get("items") or []
        return Result(self._status(data.get("status")), [
            ItemResult(str(i.get("reference", ""))[:30], "".join(ch for ch in str(i.get("number") or "") if ch.isdigit())[:17],
                       str(i.get("error") or "")[:255])
            for i in items if isinstance(i, dict)
        ])


def build_connector(name: str, *, url: str = "", token: str = "") -> CoretaxConnector:
    if name == ConnectorName.FAKE:
        return FakeConnector()
    if name == ConnectorName.HTTP:
        return HttpConnector(url=url, token=token)
    raise CoretaxError(_("Unknown Coretax connection."))
