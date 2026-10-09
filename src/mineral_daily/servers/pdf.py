from __future__ import annotations

from mcp.server import MCPServer

from mineral_daily.config import configure_logging
from mineral_daily.models import ResourceExtractionResult
from mineral_daily.services.pdf import PdfResourceService

mcp = MCPServer(
    "mineral-pdf-mcp",
    title="Mineral PDF MCP",
    description="Extract classified mineral resource rows from public technical-report PDFs.",
    instructions=(
        "Keep the reported standard, page number, evidence and confidence with every extracted row. "
        "Never relabel a JORC report as NI 43-101."
    ),
    version="0.1.0",
)
service = PdfResourceService()


@mcp.tool()
async def extract_resources(
    pdf_url: str, page_numbers: list[int] | None = None
) -> ResourceExtractionResult:
    """Extract resource rows; optional one-based page hints reduce large-PDF latency."""
    return await service.extract_resources(pdf_url=pdf_url, page_numbers=page_numbers)


def main() -> None:
    configure_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
