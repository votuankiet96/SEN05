"""Settings for historical SQL-backed OG workflows."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass


SQL_SERVER = os.environ.get("SQL_SERVER", "localhost")
SQL_DATABASE = os.environ.get("SQL_DATABASE", "SEN05_AutoTrading")
SQL_DRIVER = os.environ.get("SQL_DRIVER", "ODBC Driver 18 for SQL Server")
SQL_PORT = os.environ.get("SQL_PORT", "")
SQL_TDS_VERSION = os.environ.get("SQL_TDS_VERSION", "7.4")
SQL_UID = os.environ.get("SQL_UID", "")
SQL_PWD = os.environ.get("SQL_PWD", "")
SQL_ENCRYPT = os.environ.get("SQL_ENCRYPT", "no")
SQL_TRUST_SERVER_CERT = os.environ.get("SQL_TRUST_SERVER_CERT", "yes")


def sql_connection_string() -> str:
    """Build the ODBC connection string used by the historical data adapter."""
    if str(SQL_DRIVER).lower() == "freetds":
        server_part = f"SERVER={SQL_SERVER};"
        if SQL_PORT:
            server_part += f"PORT={SQL_PORT};"
        base = (
            f"DRIVER={{{SQL_DRIVER}}};"
            f"{server_part}"
            f"DATABASE={SQL_DATABASE};"
            f"TDS_Version={SQL_TDS_VERSION};"
        )
    else:
        base = (
            f"DRIVER={{{SQL_DRIVER}}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"Encrypt={SQL_ENCRYPT};"
            f"TrustServerCertificate={SQL_TRUST_SERVER_CERT};"
        )
    if SQL_UID and SQL_PWD:
        return base + f"UID={SQL_UID};PWD={SQL_PWD};"
    return base + "Trusted_Connection=yes;"
