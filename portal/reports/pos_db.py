"""Read-only access to the POS SQL Server (pricing) database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from django.conf import settings


class PosDbError(Exception):
    """Raised when the POS SQL Server cannot be queried."""


def pos_connection_string() -> str:
    return (getattr(settings, "POS_SQL_CONNECTION_STRING", None) or "").strip()


@contextmanager
def pos_cursor():
    conn_str = pos_connection_string()
    if not conn_str:
        raise PosDbError(
            "POS SQL connection is not configured. "
            "Set POS_SQL_CONNECTION_STRING in the environment."
        )
    try:
        import pyodbc
    except ImportError as exc:
        raise PosDbError(
            "pyodbc is required to query the POS database. "
            "Install it with: pip install pyodbc"
        ) from exc

    try:
        conn = pyodbc.connect(conn_str, timeout=10)
    except Exception as exc:  # noqa: BLE001 - surface any driver/connect failure
        raise PosDbError(f"Could not connect to POS SQL Server: {exc}") from exc

    try:
        yield conn.cursor()
    finally:
        conn.close()


def fetch_grv_purchase_lines(start_dt: datetime, end_dt: datetime) -> list[dict]:
    """
    Return GRV line items in [start_dt, end_dt), excluding cancelled GRVs.

    Party name comes from the purchase-order supplier (the only customer-like
    dimension on GRV in the pricing schema).
    """
    # Compare as naive local datetimes — GRV_Date is stored without offset.
    start = start_dt.replace(tzinfo=None) if start_dt.tzinfo else start_dt
    end = end_dt.replace(tzinfo=None) if end_dt.tzinfo else end_dt

    sql = """
        SELECT
            ISNULL(NULLIF(LTRIM(RTRIM(s.Supplier_Name)), ''), 'Unknown') AS customer_name,
            ISNULL(gi.GRVItem_Name, '') AS item_name,
            CAST(ISNULL(gi.GRVItem_Quantity, 0) AS float) AS quantity,
            CAST(ISNULL(gi.GRVItem_QuantityTotalKG, 0) AS float) AS quantity_kg,
            CAST(ISNULL(gi.GRVItem_Price, 0) AS float) AS unit_price,
            g.GRVID AS grv_id,
            g.GRV_Date AS grv_date,
            ISNULL(g.GRV_InvoiceNumber, '') AS invoice_number
        FROM GRVItem gi
        INNER JOIN GRV g ON g.GRVID = gi.GRVItem_GRVID
        LEFT JOIN PurchaseOrder po ON po.PurchaseOrderID = g.GRV_PurchaseOrderID
        LEFT JOIN Supplier s ON s.SupplierID = po.PurchaseOrder_SupplierID
        WHERE ISNULL(g.GRV_GRVStatusID, 0) <> 2
          AND g.GRV_Date >= ?
          AND g.GRV_Date < ?
        ORDER BY s.Supplier_Name, g.GRV_Date, gi.GRVItem_Name
        """

    with pos_cursor() as cur:
        cur.execute(sql, start, end)
        columns = [col[0] for col in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
