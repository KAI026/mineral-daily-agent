from __future__ import annotations

import pytest

from mineral_daily.models import ResultStatus
from mineral_daily.services.pdf import PdfResourceService, parse_resource_pages

PILBARA_PDF = "https://announcements.asx.com.au/asxpdf/20250611/pdf/06kmc3l7r1bjsm.pdf"
FENELON_PDF = (
    "https://www.wallbridgemining.com/_resources/reports/"
    "43-101_chap-00_wmfen24m117_43-101_tech_report-final.pdf?v=100110"
)
OYU_TOLGOI_REPORT = (
    "https://www.sec.gov/Archives/edgar/data/863064/000162828023004989/"
    "exhibits-963otsxk1300trs.htm"
)
SANSHANDAO_REPORT = (
    "https://static.sse.com.cn/disclosure/bond/announcement/company/c/new/"
    "2026-05-25/240785_20260525_ZIJ5.pdf"
)
KAINANTU_REPORT = (
    "https://www.k92mining.com/site/assets/files/6377/"
    "k92_updated_2024_dfs_technical_report.pdf"
)


def test_parse_ni_43_101_gold_rows() -> None:
    page = """
    NI 43-101 Technical Report
    Category Tonnage (Mt) Grade (g/t Au) Contained Gold (koz)
    Indicated 12.5 1.40 563
    Inferred 4.2 1.10 149
    """

    records, standard, warnings = parse_resource_pages([page])

    assert standard == "NI 43-101"
    assert warnings == []
    assert [record.category for record in records] == ["indicated", "inferred"]
    assert records[0].ore_quantity == 12.5
    assert records[0].grade_unit == "g/t Au"
    assert records[0].contained_metal == 563
    assert records[0].contained_unit == "koz Au"


def test_parse_jorc_lithium_rows_without_relabelling_standard() -> None:
    page = """
    JORC Code 2012 Mineral Resource
    Category Tonnes (Mt) Li2O (%) Ta2O5 (ppm) Fe2O3 (%) Li2O (Mt) Ta2O5 (M lb)
    Indicated 356 1.29 120 0.58 4.6 94
    Inferred 70 1.25 134 0.65 0.9 21
    """

    records, standard, warnings = parse_resource_pages([page])

    assert standard == "JORC"
    assert records[0].contained_metal == 4.6
    assert records[1].contained_metal == 0.9
    assert any("does not identify itself as NI 43-101" in warning for warning in warnings)


def test_missing_rows_produces_review_warning() -> None:
    records, standard, warnings = parse_resource_pages(["NI 43-101 report without a table"])

    assert records == []
    assert standard == "NI 43-101"
    assert any("no standalone" in warning for warning in warnings)
    assert ResultStatus.ERROR.value == "error"


@pytest.mark.asyncio
async def test_verified_snapshot_is_explicit_and_preserves_physical_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINERAL_AGENT_PREFER_SNAPSHOTS", "1")

    result = await PdfResourceService().extract_resources(PILBARA_PDF, [2])

    inferred = next(record for record in result.records if record.category == "inferred")
    assert result.status == ResultStatus.PARTIAL
    assert inferred.page == 2
    assert inferred.contained_metal == 0.9
    assert any("bundled text snapshot" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_real_ni43_snapshot_parses_paired_category_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINERAL_AGENT_PREFER_SNAPSHOTS", "1")

    result = await PdfResourceService().extract_resources(FENELON_PDF, [229])

    indicated = next(record for record in result.records if record.category == "indicated")
    inferred = next(record for record in result.records if record.category == "inferred")
    assert result.report_standard == "NI 43-101"
    assert indicated.ore_quantity == pytest.approx(19.79)
    assert indicated.contained_metal == 2100
    assert inferred.ore_quantity == pytest.approx(18.899)
    assert inferred.contained_metal == 2037
    assert all(record.page == 229 for record in (indicated, inferred))


@pytest.mark.asyncio
async def test_oyu_tolgoi_structured_official_snapshot_preserves_scope() -> None:
    result = await PdfResourceService().extract_resources(OYU_TOLGOI_REPORT, [17])

    assert result.status == ResultStatus.PARTIAL
    assert result.report_standard == "S-K 1300"
    assert [record.category for record in result.records] == [
        "measured_and_indicated",
        "inferred",
    ]
    assert [record.contained_metal for record in result.records] == [5.4, 13.6]
    assert all(record.contained_unit == "Mt Cu" for record in result.records)
    assert all(record.ore_quantity is None for record in result.records)
    assert result.source is not None
    assert "SEC filing" in result.source.title


@pytest.mark.asyncio
async def test_sanshandao_structured_public_disclosure_preserves_categories() -> None:
    result = await PdfResourceService().extract_resources(SANSHANDAO_REPORT, [7])

    assert result.status == ResultStatus.PARTIAL
    assert result.report_standard == "Chinese public disclosure"
    assert [record.category for record in result.records] == ["resource_total", "reserve_total"]
    assert [record.contained_metal for record in result.records] == [686.62, 340.29]
    assert all(record.contained_unit == "t Au" for record in result.records)
    assert all(record.grade is None for record in result.records)
    assert any("2025" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_kainantu_snapshot_preserves_gold_and_copper_records() -> None:
    result = await PdfResourceService().extract_resources(KAINANTU_REPORT, [7])

    assert result.status == ResultStatus.PARTIAL
    assert result.report_standard == "NI 43-101"
    assert len(result.records) == 4
    gold_records = [record for record in result.records if record.commodity == "gold"]
    copper_records = [record for record in result.records if record.commodity == "copper"]
    assert [record.contained_metal for record in gold_records] == [2.0, 3.0]
    assert [record.contained_unit for record in gold_records] == ["Moz Au", "Moz Au"]
    assert [record.contained_metal for record in copper_records] == [95.6, 248.3]
    assert [record.contained_unit for record in copper_records] == ["kt Cu", "kt Cu"]
    assert any("2023-09-12" in warning for warning in result.warnings)
