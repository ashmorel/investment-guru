import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pandas as pd


class CsvFormatError(Exception):
    pass


@dataclass(frozen=True)
class ParsedRow:
    symbol: str
    quantity: Decimal | None
    purchase_price: Decimal | None
    comment: str | None


def _dec(value) -> Decimal | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def parse_yahoo_csv(data: bytes) -> list[ParsedRow]:
    try:
        df = pd.read_csv(io.BytesIO(data), dtype=str)
    except Exception as exc:
        raise CsvFormatError(f"Unreadable CSV: {exc}") from exc
    if "Symbol" not in df.columns:
        raise CsvFormatError("No 'Symbol' column — is this a Yahoo Finance portfolio export?")

    rows: list[ParsedRow] = []
    for _, r in df.iterrows():
        symbol = (r.get("Symbol") or "").strip().upper()
        if not symbol or symbol.startswith("$$"):
            continue
        comment = r.get("Comment")
        if comment is None or pd.isna(comment):
            comment = None
        else:
            comment = str(comment).strip() or None
        rows.append(
            ParsedRow(
                symbol=symbol,
                quantity=_dec(r.get("Quantity")),
                purchase_price=_dec(r.get("Purchase Price")),
                comment=comment,
            )
        )
    return rows
