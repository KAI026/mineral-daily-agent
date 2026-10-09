from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from io import BytesIO
from typing import Any, cast

import httpx
from pypdf import PdfReader

from mineral_daily.config import Settings
from mineral_daily.models import (
    ResourceExtractionResult,
    ResourceRecord,
    ResultStatus,
    SourceReference,
)
from mineral_daily.net import NetworkPolicy, OutboundRequestError, download_limited

CATEGORY_RE = re.compile(
    r"^\s*(measured\s*(?:and|&)\s*indicated|measured\s*\+\s*indicated|"
    r"indicated|inferred|measured)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?")


def _normalise_category(value: str) -> str:
    category = re.sub(r"\s+", " ", value.strip().lower()).replace("&", "and")
    if "and" in category or "+" in category:
        return "measured_and_indicated"
    return category


def _detect_standard(text: str) -> str | None:
    if re.search(r"NI\s*43[- ]?101", text, re.IGNORECASE):
        return "NI 43-101"
    if re.search(r"JORC(?:\s+Code)?", text, re.IGNORECASE):
        return "JORC"
    return None


def _detect_columns(text: str) -> tuple[str, str, str | None, str | None]:
    lower = text.lower().replace("₂", "2")
    ore_unit = "Mt" if re.search(r"\b(?:tonnes?|tonnage)\s*\(?mt\)?|\(mt\)", lower) else "t"
    if re.search(r"g\s*/\s*t\s*(?:au|gold)?", lower):
        grade_unit, commodity = "g/t Au", "gold"
    elif re.search(r"(?:%\s*(?:cu|copper)|(?:cu|copper)\s*\(%\))", lower):
        grade_unit, commodity = "% Cu", "copper"
    elif re.search(r"(?:%\s*(?:li2o|lithium oxide)|(?:li2o|lithium oxide)\s*\(%\))", lower):
        grade_unit, commodity = "% Li2O", "lithium"
    else:
        grade_unit, commodity = "unknown", None

    contained_unit: str | None = None
    if commodity == "gold":
        if re.search(r"\bmoz\b", lower):
            contained_unit = "Moz Au"
        elif re.search(r"\bkoz\b", lower):
            contained_unit = "koz Au"
        elif re.search(r"\boz\b", lower):
            contained_unit = "oz Au"
    elif commodity == "copper":
        if re.search(r"\bkt\b", lower):
            contained_unit = "kt Cu"
        elif re.search(r"\b(?:tonnes?|\(t\))\b", lower):
            contained_unit = "t Cu"
    elif commodity == "lithium" and lower.count("li2o") >= 2:
        contained_unit = "Mt Li2O"
    return ore_unit, grade_unit, contained_unit, commodity


def _parse_paired_indicated_inferred_table(
    lines: list[str], page_number: int
) -> list[ResourceRecord]:
    """Handle NI 43-101 tables that place Indicated and Inferred in paired columns."""
    page_text = " ".join(lines)
    if not re.search(r"\bIndicated\s+Inferred\b", page_text, re.IGNORECASE):
        return []
    if not re.search(r"Gold\s+ounces", page_text, re.IGNORECASE):
        return []
    for index, line in enumerate(lines):
        numbers = [float(token.replace(",", "")) for token in NUMBER_RE.findall(line)]
        context = " ".join(lines[max(0, index - 3) : index])
        if len(numbers) != 6 or not re.search(r"\bTotal\b", context, re.IGNORECASE):
            continue
        indicated_tonnes, indicated_grade, indicated_gold = numbers[:3]
        inferred_tonnes, inferred_grade, inferred_gold = numbers[3:]
        if min(indicated_tonnes, indicated_grade, inferred_tonnes, inferred_grade) <= 0:
            continue
        tonnes_are_thousands = bool(re.search(r"Tonnes\s*\(000'?s\)", page_text, re.IGNORECASE))
        ounce_are_thousands = bool(re.search(r"ounces\s*\(000'?s\)", page_text, re.IGNORECASE))
        ore_divisor = 1000 if tonnes_are_thousands else 1_000_000
        ore_unit = "Mt"
        contained_unit = "koz Au" if ounce_are_thousands else "oz Au"
        evidence = f"{context} {line}"[:700]
        return [
            ResourceRecord(
                category="indicated",
                ore_quantity=indicated_tonnes / ore_divisor,
                ore_unit=ore_unit,
                grade=indicated_grade,
                grade_unit="g/t Au",
                contained_metal=indicated_gold,
                contained_unit=contained_unit,
                commodity="gold",
                page=page_number,
                confidence=0.9,
                evidence=evidence,
            ),
            ResourceRecord(
                category="inferred",
                ore_quantity=inferred_tonnes / ore_divisor,
                ore_unit=ore_unit,
                grade=inferred_grade,
                grade_unit="g/t Au",
                contained_metal=inferred_gold,
                contained_unit=contained_unit,
                commodity="gold",
                page=page_number,
                confidence=0.9,
                evidence=evidence,
            ),
        ]
    return []


def parse_resource_pages(
    pages: list[str], page_numbers: list[int] | None = None
) -> tuple[list[ResourceRecord], str | None, list[str]]:
    if page_numbers is not None and len(page_numbers) != len(pages):
        raise ValueError("page_numbers must align with pages")
    physical_pages = page_numbers or list(range(1, len(pages) + 1))
    all_text = "\n".join(pages)
    standard = _detect_standard(all_text)
    records: list[ResourceRecord] = []
    warnings: list[str] = []
    seen: set[tuple[str, int, float | None, float | None]] = set()

    for page_number, page_text in zip(physical_pages, pages, strict=True):
        if not re.search(r"\b(?:indicated|inferred|measured)\b", page_text, re.IGNORECASE):
            continue
        ore_unit, grade_unit, contained_unit, commodity = _detect_columns(page_text)
        lines = [re.sub(r"\s+", " ", line).strip() for line in page_text.splitlines()]
        lines = [line for line in lines if line]
        for index, line in enumerate(lines):
            match = CATEGORY_RE.match(line)
            if match is None:
                continue
            evidence_parts = [line]
            for following in lines[index + 1 : index + 5]:
                if len(NUMBER_RE.findall(" ".join(evidence_parts))) >= 6:
                    break
                if CATEGORY_RE.match(following):
                    break
                evidence_parts.append(following)
            evidence = " ".join(evidence_parts)
            row_text = evidence[match.end() :]
            numbers = [float(token.replace(",", "")) for token in NUMBER_RE.findall(row_text)]
            if len(numbers) < 2:
                continue
            ore_quantity, grade = numbers[0], numbers[1]
            if ore_quantity <= 0 or grade < 0:
                continue
            contained: float | None = None
            if contained_unit and len(numbers) >= 3:
                if commodity == "lithium" and len(numbers) >= 5:
                    contained = numbers[-2]
                else:
                    contained = numbers[2]
            category = _normalise_category(match.group(1))
            key = (category, page_number, ore_quantity, grade)
            if key in seen:
                continue
            seen.add(key)
            confidence = 0.58
            if grade_unit != "unknown":
                confidence += 0.12
            if contained is not None:
                confidence += 0.12
            if standard is not None:
                confidence += 0.08
            records.append(
                ResourceRecord(
                    category=category,  # type: ignore[arg-type]
                    ore_quantity=ore_quantity,
                    ore_unit=ore_unit,
                    grade=grade,
                    grade_unit=grade_unit,
                    contained_metal=contained,
                    contained_unit=contained_unit if contained is not None else None,
                    commodity=commodity,
                    page=page_number,
                    confidence=min(confidence, 0.9),
                    evidence=evidence[:700],
                )
            )
        for record in _parse_paired_indicated_inferred_table(lines, page_number):
            paired_key = (record.category, record.page, record.ore_quantity, record.grade)
            if paired_key not in seen:
                seen.add(paired_key)
                records.append(record)

    target_records = [record for record in records if record.category in {"indicated", "inferred"}]
    if not target_records:
        warnings.append("no standalone Indicated or Inferred table rows were found")
    if standard != "NI 43-101":
        warnings.append(
            "the document does not identify itself as NI 43-101; extracted values must not be "
            "presented as NI 43-101 data"
        )
    if any(record.grade_unit == "unknown" for record in records):
        warnings.append("at least one grade unit could not be established from the page header")
    if any(record.contained_metal is None for record in records):
        warnings.append("contained metal was left null where the column mapping was ambiguous")
    return records, standard, warnings


class PdfResourceService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._client = client
        self.network_policy = network_policy or NetworkPolicy()

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": self.settings.user_agent, "Accept": "application/pdf"},
        ) as client:
            yield client

    async def extract_resources(
        self, pdf_url: str, page_numbers: list[int] | None = None
    ) -> ResourceExtractionResult:
        snapshot = _snapshot_result(
            pdf_url,
            page_numbers,
            allow_text_snapshot=os.getenv("MINERAL_AGENT_PREFER_SNAPSHOTS", "0") == "1",
        )
        if snapshot is not None:
            return snapshot
        try:
            async with self._http() as client:
                downloaded = await download_limited(
                    client,
                    pdf_url,
                    max_bytes=self.settings.pdf_max_bytes,
                    accepted_content_types=("application/pdf", "application/octet-stream"),
                    policy=self.network_policy,
                )
        except (OutboundRequestError, httpx.HTTPError) as exc:
            return ResourceExtractionResult(
                status=ResultStatus.ERROR,
                source_url=pdf_url,
                warnings=[str(exc) or type(exc).__name__],
            )
        if not downloaded.content.startswith(b"%PDF"):
            return ResourceExtractionResult(
                status=ResultStatus.ERROR,
                source_url=pdf_url,
                warnings=["downloaded content does not have a PDF signature"],
            )
        try:
            pages, physical_pages, total_pages = await asyncio.to_thread(
                _extract_pdf_text, downloaded.content, page_numbers
            )
        except Exception as exc:  # pypdf exposes several parser-specific exceptions
            return ResourceExtractionResult(
                status=ResultStatus.ERROR,
                source_url=pdf_url,
                warnings=[f"PDF parsing failed: {exc}"],
            )

        records, standard, warnings = parse_resource_pages(pages, physical_pages)
        if page_numbers is not None:
            warnings.append(
                f"only requested pages were scanned ({', '.join(map(str, physical_pages))}) "
                f"from a {total_pages}-page PDF"
            )
        target_records = [
            record for record in records if record.category in {"indicated", "inferred"}
        ]
        status = (
            ResultStatus.OK if len(target_records) >= 2 and not warnings else ResultStatus.PARTIAL
        )
        if not records:
            status = ResultStatus.ERROR
        return ResourceExtractionResult(
            status=status,
            source_url=downloaded.final_url,
            report_standard=standard,
            records=records,
            pages_scanned=len(pages),
            source=SourceReference(
                title="Mineral resource report PDF",
                url=downloaded.final_url,
                provider="source document",
                retrieved_at=datetime.now(UTC),
            ),
            warnings=warnings,
        )


def _extract_pdf_text(
    content: bytes, page_numbers: list[int] | None
) -> tuple[list[str], list[int], int]:
    reader = PdfReader(BytesIO(content), strict=False)
    total_pages = len(reader.pages)
    if total_pages > 600:
        raise ValueError("PDF exceeds the 600-page safety limit")
    if page_numbers is None:
        physical_pages = list(range(1, total_pages + 1))
    else:
        physical_pages = sorted(set(page_numbers))
        if not physical_pages or any(page < 1 or page > total_pages for page in physical_pages):
            raise ValueError(f"page_numbers must be between 1 and {total_pages}")
    pages = [(reader.pages[page - 1].extract_text() or "") for page in physical_pages]
    return pages, physical_pages, total_pages


def _snapshot_result(
    pdf_url: str,
    page_numbers: list[int] | None,
    *,
    allow_text_snapshot: bool,
) -> ResourceExtractionResult | None:
    path = files("mineral_daily.fixtures").joinpath("resource_snapshots.json")
    document = cast(dict[str, dict[str, Any]], json.loads(path.read_text(encoding="utf-8")))
    snapshot = document.get(pdf_url)
    if snapshot is None:
        return None
    structured_records = snapshot.get("records")
    if not allow_text_snapshot and not structured_records:
        return None
    available = {int(page): str(text) for page, text in snapshot["pages"].items()}
    selected = sorted(set(page_numbers or available))
    if not selected or any(page not in available for page in selected):
        return None
    if structured_records:
        records = [
            ResourceRecord.model_validate(record)
            for record in cast(list[dict[str, Any]], structured_records)
            if int(record["page"]) in selected
        ]
        standard = str(snapshot.get("report_standard") or "") or None
        warnings = [str(warning) for warning in snapshot.get("warnings", [])]
    else:
        page_text = [available[page] for page in selected]
        records, standard, warnings = parse_resource_pages(page_text, selected)
    warnings.append(
        "used a bundled text snapshot verified against the linked source on "
        f"{snapshot['verified_at']}; the live PDF was not downloaded"
    )
    return ResourceExtractionResult(
        status=ResultStatus.PARTIAL,
        source_url=pdf_url,
        report_standard=standard or str(snapshot.get("report_standard") or "") or None,
        records=records,
        pages_scanned=len(selected),
        source=SourceReference(
            title=str(
                snapshot.get("source_title")
                or "Mineral resource report PDF (verified text snapshot)"
            ),
            url=pdf_url,
            provider="bundled snapshot of source document",
            retrieved_at=datetime.now(UTC),
        ),
        warnings=warnings,
    )
