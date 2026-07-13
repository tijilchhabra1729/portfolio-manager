from __future__ import annotations

import io
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.auth.supabase_jwt import current_user
from app.core.sectors import MARKETS, Market
from app.ingest.template_writer import build_workbook
from app.services import portfolio_service
from app.services.portfolio_service import UploadMode
from app.store.db import connect

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_BYTES = 5 * 1024 * 1024


async def _read(upload: UploadFile, allow_csv: bool = False) -> bytes:
    allowed = (".xlsx", ".xlsm", ".csv") if allow_csv else (".xlsx", ".xlsm")
    if not (upload.filename or "").lower().endswith(allowed):
        raise HTTPException(
            400,
            "Upload an .xlsx from the template, or a .csv exported from your broker."
            if allow_csv
            else "Upload an .xlsx file (start from the template).",
        )
    content = await upload.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(400, "File is too large (5 MB max).")
    return content


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    mode: UploadMode = Form(UploadMode.APPEND),
    market: Market | None = Form(None),
    keep_custom_sectors: bool = Form(False),
    user_id: str = Depends(current_user),
):
    """Both of the doc's upload requirements. mode=replace is the bulk upload (wipes and
    reloads the markets present in the file); mode=append is the incremental upload.

    `market` is only consulted for a CSV. Our workbook carries the market in its sheet
    names; a broker export does not carry it at all.
    """
    content = await _read(file, allow_csv=True)
    with connect() as conn:
        result = portfolio_service.upload(
            conn,
            user_id,
            content,
            mode,
            filename=file.filename or "upload.xlsx",
            market=market,
            keep_custom_sectors=keep_custom_sectors,
        )
        if not result.ok:
            # Roll back rather than commit a partial portfolio.
            conn.rollback()
            raise HTTPException(422, detail={"errors": result.errors})
    return {
        "ok": True,
        "mode": result.mode.value,
        "transactions_added": result.transactions_added,
        "markets": result.markets,
        "warnings": result.warnings,
    }


@router.post("/delete")
async def delete(
    file: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    content = await _read(file)
    with connect() as conn:
        result = portfolio_service.delete_units(
            conn, user_id, content, filename=file.filename or "delete.xlsx"
        )
        if not result.ok:
            conn.rollback()
            raise HTTPException(422, detail={"errors": result.errors})
    return {"ok": True, "removed": result.removed}


@router.get("/template")
def template(kind: str = "blank"):
    """kind=blank is the empty template to fill in; kind=sample is the same file with
    example rows, so the expected format is visible."""
    if kind not in ("blank", "sample"):
        raise HTTPException(400, "kind must be 'blank' or 'sample'.")

    path = Path("/tmp") / f"portfolio_{kind}.xlsx"
    build_workbook(path, samples=(kind == "sample"))
    name = (
        "portfolio_upload_template.xlsx" if kind == "blank" else "portfolio_sample.xlsx"
    )
    return StreamingResponse(
        io.BytesIO(path.read_bytes()),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/markets")
def markets():
    return [
        {
            "code": spec.code.value,
            "label": spec.label,
            "currency": spec.currency,
            "symbol": spec.symbol,
            "sectors": list(spec.sectors),
        }
        for spec in MARKETS.values()
    ]
