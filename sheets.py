# -*- coding: utf-8 -*-
"""
Вся работа с Google Sheets — тут и только тут.
Таблица используется как база данных: один лист "Clients", одна строка = один клиент.

Публичные функции:
    get_gspread_client()       -> авторизованный gspread.Client
    get_or_create_worksheet()  -> gspread.Worksheet (создаёт лист с заголовками, если его нет)
    load_clients_df()          -> pandas.DataFrame со всеми клиентами
    append_client(data)        -> добавляет новую строку, возвращает присвоенный id
    append_clients(list)       -> то же самое, но пачкой (один запрос к API)
    update_client(id, updates) -> обновляет только переданные поля для клиента с этим id
    update_clients(dict)       -> то же самое, но для нескольких клиентов сразу
    get_client_by_id(id)       -> dict с данными одного клиента (или None)

    Второй лист "Recherches" — история поисков прослойки (вкладка Prospection):
    log_prospect_search(...)     -> записывает одну строку поиска, возвращает её номер
    update_search_log_added(...) -> дозаполняет колонку "ajoutes" для этой строки
    load_search_log_df()         -> pandas.DataFrame с историей поисков
"""

import time
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

# Streamlit relance TOUT le script à chaque clic/interaction — donc sans cache,
# ouvrir la feuille (client.open + vérif des en-têtes) est refait à chaque fois,
# pour chaque fonction qui en a besoin. Ça consomme le quota Google Sheets API
# ("429 Quota exceeded") très vite dès qu'il y a plusieurs actions d'affilée.
# On garde donc le classeur (Spreadsheet) en cache quelques secondes.
_SHEET_CACHE_TTL_SECONDS = 20
_sheet_cache = {"sheet": None, "ts": 0.0}
_ws_cache = {"ws": None, "ts": 0.0}
_search_log_ws_cache = {"ws": None, "ts": 0.0}

# Historique des recherches de prospection (onglet "Prospection") — un second
# onglet dans la même Google Sheet, pour garder trace de ce qui a déjà été
# cherché (ville, secteurs, date) et ne pas s'y perdre au bout de plusieurs
# semaines d'utilisation.
SEARCH_LOG_WORKSHEET = "Recherches"
SEARCH_LOG_COLUMNS = ["date", "city", "sectors", "elargi_environs", "trouves", "ajoutes"]


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


def _get_spreadsheet(force_refresh: bool = False):
    now = time.time()
    if (
        not force_refresh
        and _sheet_cache["sheet"] is not None
        and (now - _sheet_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _sheet_cache["sheet"]

    client = get_gspread_client()
    try:
        sheet = client.open(config.GOOGLE_SHEET_NAME)
    except gspread.SpreadsheetNotFound as exc:
        raise RuntimeError(
            f"Google-таблица '{config.GOOGLE_SHEET_NAME}' не найдена. "
            "Создай её вручную и дай доступ сервис-аккаунту (см. README, шаг 3)."
        ) from exc
    except gspread.exceptions.APIError as exc:
        # Streamlit часто обрезает/прячет исходный текст ошибки от gspread,
        # поэтому вытаскиваем код и текст ответа Google API вручную, чтобы
        # было видно, ЧТО именно случилось (лимит запросов, права доступа и т.п.)
        status = None
        body_text = ""
        try:
            status = exc.response.status_code
            body_text = exc.response.text[:500]
        except Exception:
            pass

        if status == 429:
            hint = (
                "Google Sheets API временно ограничил количество запросов "
                "(слишком много обращений подряд). Подожди 1-2 минуты и обнови "
                "страницу — обычно само проходит."
            )
        elif status == 403:
            hint = (
                "Нет доступа к таблице. Проверь, что таблица "
                f"'{config.GOOGLE_SHEET_NAME}' расшарена именно на email "
                "сервис-аккаунта (client_email из Secrets), с правом Editor."
            )
        elif status == 404:
            hint = (
                f"Таблица '{config.GOOGLE_SHEET_NAME}' не найдена сервис-аккаунтом "
                "— проверь точное название и что доступ дан."
            )
        else:
            hint = "См. код и текст ответа Google ниже."

        raise RuntimeError(
            f"Ошибка Google Sheets API (код {status}). {hint}\n\nОтвет Google: {body_text}"
        ) from exc

    _sheet_cache["sheet"] = sheet
    _sheet_cache["ts"] = now
    return sheet


def get_or_create_worksheet(force_refresh: bool = False) -> gspread.Worksheet:
    now = time.time()
    if (
        not force_refresh
        and _ws_cache["ws"] is not None
        and (now - _ws_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _ws_cache["ws"]

    sheet = _get_spreadsheet(force_refresh)

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

    _ws_cache["ws"] = ws
    _ws_cache["ts"] = now
    return ws


def get_or_create_search_log_worksheet(force_refresh: bool = False) -> gspread.Worksheet:
    """Onglet séparé 'Recherches' — historique des recherches de prospection."""
    now = time.time()
    if (
        not force_refresh
        and _search_log_ws_cache["ws"] is not None
        and (now - _search_log_ws_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _search_log_ws_cache["ws"]

    sheet = _get_spreadsheet(force_refresh)

    try:
        ws = sheet.worksheet(SEARCH_LOG_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(
            title=SEARCH_LOG_WORKSHEET, rows=1000, cols=len(SEARCH_LOG_COLUMNS)
        )
        ws.append_row(SEARCH_LOG_COLUMNS)

    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(SEARCH_LOG_COLUMNS)

    _search_log_ws_cache["ws"] = ws
    _search_log_ws_cache["ts"] = now
    return ws


def log_prospect_search(city: str, sectors: list, nearby: bool, found_count: int, added_count: int = 0) -> int:
    """Enregistre une recherche de prospection dans l'onglet 'Recherches', pour
    garder une trace de ce qui a déjà été cherché (ville, secteurs, date).
    Renvoie le numéro de la ligne créée (utile pour mettre à jour "ajoutes"
    plus tard, quand l'utilisateur valide l'ajout — voir update_search_log_added)."""
    ws = get_or_create_search_log_worksheet()
    row_number = len(ws.get_all_values()) + 1
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        city,
        ", ".join(sectors) if sectors else "(tous les secteurs)",
        "oui" if nearby else "non",
        found_count,
        added_count,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    return row_number


def update_search_log_added(row_number: int, added_count: int) -> None:
    """Met à jour la colonne 'ajoutes' d'une ligne déjà loggée (voir log_prospect_search)."""
    ws = get_or_create_search_log_worksheet()
    col_index = SEARCH_LOG_COLUMNS.index("ajoutes") + 1
    ws.update_cell(row_number, col_index, added_count)


def load_search_log_df() -> pd.DataFrame:
    ws = get_or_create_search_log_worksheet()
    records = ws.get_all_records()
    df = pd.DataFrame(records, columns=SEARCH_LOG_COLUMNS)
    return df


def load_clients_df() -> pd.DataFrame:
    ws = get_or_create_worksheet()
    records = ws.get_all_records()  # список dict, ключи = заголовки
    df = pd.DataFrame(records, columns=config.CLIENT_COLUMNS)
    if df.empty:
        return df
    # приводим числовые/строковые поля к удобному виду
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
    # строки без валидного id (например, случайная пустая строка в таблице
    # после ручного удаления клиентов) ломают выпадающие списки в интерфейсе
    # (Streamlit не умеет сравнивать <NA>) — просто игнорируем такие строки.
    df = df[df["id"].notna()].reset_index(drop=True)
    if df.empty:
        return df
    df["fit_score"] = pd.to_numeric(df["fit_score"], errors="coerce")
    df["relance_count"] = pd.to_numeric(df["relance_count"], errors="coerce").fillna(0).astype(int)
    return df


def _next_id(df: pd.DataFrame) -> int:
    if df.empty or df["id"].isna().all():
        return 1
    return int(df["id"].max()) + 1


def append_clients(data_list: list) -> list:
    """
    Ajoute plusieurs clients en UN SEUL appel à l'API (une lecture pour les id,
    une écriture pour toutes les lignes) — à utiliser dès qu'on ajoute plus
    d'un client d'un coup (ex: import de prospects), pour éviter d'épuiser le
    quota Google Sheets avec des appels répétés.
    Renvoie la liste des id attribués, dans le même ordre que data_list.
    """
    if not data_list:
        return []

    df = load_clients_df()
    next_id = _next_id(df)
    today = datetime.now().strftime("%Y-%m-%d")

    ordered_rows = []
    new_ids = []
    for data in data_list:
        row = {col: "" for col in config.CLIENT_COLUMNS}
        row.update(data)
        row["id"] = next_id
        if not row.get("status"):
            row["status"] = "Nouveau"
        if not row.get("date_added"):
            row["date_added"] = today
        row["relance_count"] = row.get("relance_count") or 0
        ordered_rows.append([row.get(col, "") for col in config.CLIENT_COLUMNS])
        new_ids.append(next_id)
        next_id += 1

    ws = get_or_create_worksheet()
    ws.append_rows(ordered_rows, value_input_option="USER_ENTERED")
    return new_ids


def append_client(data: dict) -> int:
    """Добавляет одного клиента. Для нескольких сразу — используй append_clients()."""
    return append_clients([data])[0]


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


def update_clients(updates_by_id: dict) -> int:
    """
    Met à jour plusieurs clients en UN SEUL appel à l'API (une lecture pour
    localiser les lignes, une écriture pour tous les changements) — à utiliser
    dès qu'on met à jour plusieurs clients d'un coup (ex: "Scorer tous les
    clients en attente"), pour éviter d'épuiser le quota Google Sheets.
    updates_by_id = {client_id: {colonne: valeur, ...}, ...}
    Renvoie le nombre de clients effectivement trouvés et mis à jour.
    """
    if not updates_by_id:
        return 0

    ws = get_or_create_worksheet()
    id_col_index = config.CLIENT_COLUMNS.index("id") + 1
    col_values = ws.col_values(id_col_index)
    row_by_id = {}
    for i, val in enumerate(col_values):
        if i == 0:
            continue  # заголовок
        try:
            row_by_id[int(str(val).strip())] = i + 1
        except ValueError:
            continue

    cell_updates = []
    updated_count = 0
    for client_id, updates in updates_by_id.items():
        row_number = row_by_id.get(int(client_id))
        if row_number is None:
            continue
        updated_count += 1
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
    return updated_count


def update_client(client_id: int, updates: dict) -> bool:
    """Обновляет одного клиента. Для нескольких сразу — используй update_clients()."""
    return update_clients({client_id: updates}) > 0


def get_client_by_id(client_id: int, df: Optional[pd.DataFrame] = None) -> Optional[dict]:
    """
    Если df передан (например уже загруженный и закэшированный в app.py) —
    используем его вместо нового обращения к Google Sheets. Это сильно
    снижает число запросов: Streamlit заново выполняет весь скрипт при любом
    клике, а без этого каждая вкладка дёргала бы API отдельно на каждый клик.
    """
    if df is None:
        df = load_clients_df()
    if df.empty:
        return None
    match = df[df["id"] == client_id]
    if match.empty:
        return None
    return match.iloc[0].to_dict()
