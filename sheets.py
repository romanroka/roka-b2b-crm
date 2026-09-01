# -*- coding: utf-8 -*-
"""
Вся работа с Google Sheets — тут и только тут.
Таблица используется как база данных: один лист "Clients", одна строка = один клиент.

Публичные функции:
    get_gspread_client()       -> авторизованный gspread.Client
    get_or_create_worksheet()  -> gspread.Worksheet (создаёт лист с заголовками, если его нет)
    load_clients_df()          -> pandas.DataFrame со всеми клиентами
    append_client(data)        -> добавляет новую строку, возвращает присвоенный id
    update_client(id, updates) -> обновляет только переданные поля для клиента с этим id
    get_client_by_id(id)       -> dict с данными одного клиента (или None)
"""

from datetime import datetime
from typing import Optional

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_gspread_client() -> gspread.Client:
    """
    Поддерживает два способа передать ключ сервис-аккаунта:
    1) Streamlit Secrets, ключ "gcp_service_account" — так работает при деплое
       на Streamlit Community Cloud (без файла и без терминала).
    2) Локальный файл credentials.json — так работает при запуске на своём
       компьютере (см. README).
    """
    service_account_info = None
    try:
        if "gcp_service_account" in st.secrets:
            service_account_info = dict(st.secrets["gcp_service_account"])
    except Exception:
        service_account_info = None

    if service_account_info:
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
    return gspread.authorize(creds)


def get_or_create_worksheet() -> gspread.Worksheet:
    client = get_gspread_client()
    try:
        sheet = client.open(config.GOOGLE_SHEET_NAME)
    except gspread.SpreadsheetNotFound as exc:
        raise RuntimeError(
            f"Google-таблица '{config.GOOGLE_SHEET_NAME}' не найдена. "
            "Создай её вручную и дай доступ сервис-аккаунту (см. README, шаг 3)."
        ) from exc

    try:
        ws = sheet.worksheet(config.CLIENTS_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(
            title=config.CLIENTS_WORKSHEET, rows=1000, cols=len(config.CLIENT_COLUMNS)
        )
        ws.append_row(config.CLIENT_COLUMNS)

    # если лист есть, но пустой (без заголовков) — допишем заголовки
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(config.CLIENT_COLUMNS)
    else:
        # если в коде появились новые колонки (например "website"), которых
        # ещё нет в уже существующей таблице — дописываем их в конец строки
        # заголовков, ничего не переставляя и не удаляя.
        missing = [c for c in config.CLIENT_COLUMNS if c not in first_row]
        if missing:
            start_col = len(first_row) + 1
            end_col = start_col + len(missing) - 1
            if ws.col_count < end_col:
                # сама таблица физически ещё не такая широкая — расширяем сетку,
                # иначе Google Sheets откажет с "exceeds grid limits"
                ws.resize(cols=end_col)
            cell_range = (
                f"{gspread.utils.rowcol_to_a1(1, start_col)}:"
                f"{gspread.utils.rowcol_to_a1(1, end_col)}"
            )
            ws.update(cell_range, [missing])

    return ws


def load_clients_df() -> pd.DataFrame:
    ws = get_or_create_worksheet()
    records = ws.get_all_records()  # список dict, ключи = заголовки
    df = pd.DataFrame(records, columns=config.CLIENT_COLUMNS)
    if df.empty:
        return df
    # приводим числовые/строковые поля к удобному виду
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
    df["fit_score"] = pd.to_numeric(df["fit_score"], errors="coerce")
    df["relance_count"] = pd.to_numeric(df["relance_count"], errors="coerce").fillna(0).astype(int)
    return df


def _next_id(df: pd.DataFrame) -> int:
    if df.empty or df["id"].isna().all():
        return 1
    return int(df["id"].max()) + 1


def append_client(data: dict) -> int:
    """Добавляет клиента. data может содержать только часть полей — остальные пустые."""
    df = load_clients_df()
    new_id = _next_id(df)

    row = {col: "" for col in config.CLIENT_COLUMNS}
    row.update(data)
    row["id"] = new_id
    row.setdefault("date_added", datetime.now().strftime("%Y-%m-%d"))
    if not row.get("status"):
        row["status"] = "Nouveau"
    if not row.get("date_added"):
        row["date_added"] = datetime.now().strftime("%Y-%m-%d")
    row["relance_count"] = row.get("relance_count") or 0

    ws = get_or_create_worksheet()
    ordered_row = [row.get(col, "") for col in config.CLIENT_COLUMNS]
    ws.append_row(ordered_row, value_input_option="USER_ENTERED")
    return new_id


def _find_row_number(ws: gspread.Worksheet, client_id: int) -> Optional[int]:
    """Возвращает номер строки (1-based, с учётом заголовка) для клиента с данным id."""
    id_col_index = config.CLIENT_COLUMNS.index("id") + 1
    col_values = ws.col_values(id_col_index)
    for i, val in enumerate(col_values):
        if i == 0:
            continue  # заголовок
        if str(val).strip() == str(client_id):
            return i + 1  # gspread строки 1-based
    return None


def update_client(client_id: int, updates: dict) -> bool:
    """Обновляет только переданные поля (updates = {column_name: value}) для клиента с client_id."""
    ws = get_or_create_worksheet()
    row_number = _find_row_number(ws, client_id)
    if row_number is None:
        return False

    cell_updates = []
    for col_name, value in updates.items():
        if col_name not in config.CLIENT_COLUMNS:
            continue
        col_index = config.CLIENT_COLUMNS.index(col_name) + 1
        cell_updates.append(
            {
                "range": gspread.utils.rowcol_to_a1(row_number, col_index),
                "values": [[value]],
            }
        )

    if cell_updates:
        ws.batch_update(cell_updates, value_input_option="USER_ENTERED")
    return True


def get_client_by_id(client_id: int) -> Optional[dict]:
    df = load_clients_df()
    if df.empty:
        return None
    match = df[df["id"] == client_id]
    if match.empty:
        return None
    return match.iloc[0].to_dict()
