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

    Третий лист "Config" — данные о бренде/компании (вкладка Paramètres):
    load_brand_settings()        -> dict {ключ: значение}
    save_brand_settings(dict)    -> полностью перезаписывает эти настройки

    Четвёртый лист "Messages" — история ВСЕХ отправленных сообщений:
    log_message(id, company, type_, texte) -> добавляет одну запись в историю
    load_messages_df()                     -> pandas.DataFrame со всей историей
    get_messages_for_client(id, df=None)   -> история одного клиента

    Gmail-интеграция (листы "GmailAuth", "EmailThreads", "EmailMessages"):
    load_gmail_auth() / save_gmail_auth(dict) / disconnect_gmail()
    load_email_threads_df() / upsert_email_threads(list)
    get_existing_message_ids() / append_email_messages(list)
    load_email_messages_for_thread(thread_id)
    find_or_create_client_for_email(email, display_name)
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
_config_ws_cache = {"ws": None, "ts": 0.0}

# Historique des recherches de prospection (onglet "Prospection") — un second
# onglet dans la même Google Sheet, pour garder trace de ce qui a déjà été
# cherché (ville, secteurs, date) et ne pas s'y perdre au bout de plusieurs
# semaines d'utilisation.
SEARCH_LOG_WORKSHEET = "Recherches"
SEARCH_LOG_COLUMNS = ["date", "city", "sectors", "elargi_environs", "trouves", "ajoutes"]

# Informations sur l'entreprise/marque pour laquelle l'appli travaille — un
# troisième onglet, en clé/valeur, rempli depuis l'onglet "⚙️ Paramètres" de
# l'appli. Sert à personnaliser les emails générés par l'IA (à quoi sert
# l'entreprise, quel produit elle vend...) sans jamais toucher au code ni aux
# Secrets — pratique pour configurer l'appli pour un nouveau client.
CONFIG_WORKSHEET = "Config"
CONFIG_HEADER = ["cle", "valeur"]

# Historique complet des messages envoyés à chaque client (un quatrième
# onglet) — contrairement à client["letter_text"] qui ne garde que le
# DERNIER texte généré, ceci garde TOUT l'historique (premier email, chaque
# relance, échantillons) pour pouvoir le consulter depuis la fiche client.
MESSAGES_WORKSHEET = "Messages"
MESSAGES_COLUMNS = ["date", "client_id", "company", "type", "texte"]
_messages_ws_cache = {"ws": None, "ts": 0.0}

# ---------------------------------------------------------------------------
# Gmail — connexion OAuth (clé/valeur, comme "Config") + historique des
# threads et des messages importés. Voir gmail_sync.py pour toute la logique
# d'import/synchronisation ; ce fichier ne fait que stocker/relire.
# ---------------------------------------------------------------------------
GMAIL_AUTH_WORKSHEET = "GmailAuth"
GMAIL_AUTH_HEADER = ["cle", "valeur"]
_gmail_auth_ws_cache = {"ws": None, "ts": 0.0}

EMAIL_THREADS_WORKSHEET = "EmailThreads"
EMAIL_THREADS_COLUMNS = [
    "thread_id", "client_id", "contact_email", "subject",
    "first_message_date", "last_inbound_date", "last_outbound_date",
    "message_count", "ai_stage", "ai_stage_reasoning",
    "next_action", "next_follow_up_date", "last_synced_at",
]
_email_threads_ws_cache = {"ws": None, "ts": 0.0}

EMAIL_MESSAGES_WORKSHEET = "EmailMessages"
EMAIL_MESSAGES_COLUMNS = [
    "message_id", "thread_id", "direction", "date",
    "from_email", "to_email", "subject", "body_text",
]
_email_messages_ws_cache = {"ws": None, "ts": 0.0}


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


def get_or_create_config_worksheet(force_refresh: bool = False) -> gspread.Worksheet:
    """Onglet séparé 'Config' — infos sur l'entreprise/marque, en clé/valeur."""
    now = time.time()
    if (
        not force_refresh
        and _config_ws_cache["ws"] is not None
        and (now - _config_ws_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _config_ws_cache["ws"]

    sheet = _get_spreadsheet(force_refresh)

    try:
        ws = sheet.worksheet(CONFIG_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=CONFIG_WORKSHEET, rows=50, cols=2)
        ws.append_row(CONFIG_HEADER)

    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(CONFIG_HEADER)

    _config_ws_cache["ws"] = ws
    _config_ws_cache["ts"] = now
    return ws


def load_brand_settings() -> dict:
    """Renvoie {cle: valeur} depuis l'onglet 'Config'. Vide si rien n'a
    encore été enregistré (première utilisation) — à ce moment-là, l'appli
    retombe sur les valeurs par défaut de config.py."""
    ws = get_or_create_config_worksheet()
    records = ws.get_all_records()  # [{"cle": ..., "valeur": ...}, ...]
    return {
        str(r.get("cle", "")).strip(): r.get("valeur", "")
        for r in records
        if str(r.get("cle", "")).strip()
    }


def save_brand_settings(settings: dict) -> None:
    """Réécrit entièrement l'onglet 'Config' avec les valeurs données.
    Petit volume de données (une dizaine de lignes) — pas besoin d'un
    update ligne par ligne, une réécriture complète est plus simple et fiable."""
    ws = get_or_create_config_worksheet(force_refresh=True)
    rows = [CONFIG_HEADER] + [[str(k), "" if v is None else str(v)] for k, v in settings.items()]
    ws.clear()
    ws.update("A1", rows)
    # on force une relecture propre au prochain appel (clear() invaliderait
    # sinon un ws caché avec l'ancienne géométrie/contenu)
    _config_ws_cache["ts"] = 0.0


def get_or_create_messages_worksheet(force_refresh: bool = False) -> gspread.Worksheet:
    """Onglet séparé 'Messages' — historique complet des messages envoyés."""
    now = time.time()
    if (
        not force_refresh
        and _messages_ws_cache["ws"] is not None
        and (now - _messages_ws_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _messages_ws_cache["ws"]

    sheet = _get_spreadsheet(force_refresh)

    try:
        ws = sheet.worksheet(MESSAGES_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(
            title=MESSAGES_WORKSHEET, rows=2000, cols=len(MESSAGES_COLUMNS)
        )
        ws.append_row(MESSAGES_COLUMNS)

    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(MESSAGES_COLUMNS)

    _messages_ws_cache["ws"] = ws
    _messages_ws_cache["ts"] = now
    return ws


def log_message(client_id: int, company: str, type_: str, texte: str = "") -> None:
    """Ajoute une ligne à l'historique des messages envoyés — à appeler à
    chaque fois qu'un message est vraiment marqué comme envoyé (premier
    email, relance, échantillons...), jamais pour un simple brouillon."""
    ws = get_or_create_messages_worksheet()
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        client_id,
        company,
        type_,
        texte,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")


def load_messages_df() -> pd.DataFrame:
    ws = get_or_create_messages_worksheet()
    records = ws.get_all_records()
    df = pd.DataFrame(records, columns=MESSAGES_COLUMNS)
    if not df.empty:
        df["client_id"] = pd.to_numeric(df["client_id"], errors="coerce").astype("Int64")
    return df


def get_messages_for_client(client_id: int, messages_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Si messages_df est déjà chargé (recommandé, pour éviter un appel API
    en plus), on filtre dessus. Sinon on le charge à la demande."""
    if messages_df is None:
        messages_df = load_messages_df()
    if messages_df.empty:
        return messages_df
    return messages_df[messages_df["client_id"] == client_id].sort_values("date")


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


# ---------------------------------------------------------------------------
# Gmail — connexion (GmailAuth)
# ---------------------------------------------------------------------------
def get_or_create_gmail_auth_worksheet(force_refresh: bool = False) -> gspread.Worksheet:
    now = time.time()
    if (
        not force_refresh
        and _gmail_auth_ws_cache["ws"] is not None
        and (now - _gmail_auth_ws_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _gmail_auth_ws_cache["ws"]

    sheet = _get_spreadsheet(force_refresh)
    try:
        ws = sheet.worksheet(GMAIL_AUTH_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(title=GMAIL_AUTH_WORKSHEET, rows=20, cols=2)
        ws.append_row(GMAIL_AUTH_HEADER)

    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(GMAIL_AUTH_HEADER)

    _gmail_auth_ws_cache["ws"] = ws
    _gmail_auth_ws_cache["ts"] = now
    return ws


def load_gmail_auth() -> dict:
    """{cle: valeur} — contient access_token, refresh_token, token_expiry,
    email_address, last_synced_at une fois Gmail connecté. Vide si jamais
    connecté ou après disconnect_gmail()."""
    ws = get_or_create_gmail_auth_worksheet()
    records = ws.get_all_records()
    return {
        str(r.get("cle", "")).strip(): r.get("valeur", "")
        for r in records
        if str(r.get("cle", "")).strip()
    }


def save_gmail_auth(auth: dict) -> None:
    ws = get_or_create_gmail_auth_worksheet(force_refresh=True)
    rows = [GMAIL_AUTH_HEADER] + [[str(k), "" if v is None else str(v)] for k, v in auth.items()]
    ws.clear()
    ws.update("A1", rows)
    _gmail_auth_ws_cache["ts"] = 0.0


def disconnect_gmail() -> None:
    """Efface les infos de connexion Gmail (l'utilisateur devra se reconnecter)."""
    save_gmail_auth({})


# ---------------------------------------------------------------------------
# Gmail — threads (EmailThreads)
# ---------------------------------------------------------------------------
def get_or_create_email_threads_worksheet(force_refresh: bool = False) -> gspread.Worksheet:
    now = time.time()
    if (
        not force_refresh
        and _email_threads_ws_cache["ws"] is not None
        and (now - _email_threads_ws_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _email_threads_ws_cache["ws"]

    sheet = _get_spreadsheet(force_refresh)
    try:
        ws = sheet.worksheet(EMAIL_THREADS_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(
            title=EMAIL_THREADS_WORKSHEET, rows=5000, cols=len(EMAIL_THREADS_COLUMNS)
        )
        ws.append_row(EMAIL_THREADS_COLUMNS)

    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(EMAIL_THREADS_COLUMNS)

    _email_threads_ws_cache["ws"] = ws
    _email_threads_ws_cache["ts"] = now
    return ws


def load_email_threads_df() -> pd.DataFrame:
    ws = get_or_create_email_threads_worksheet()
    records = ws.get_all_records()
    df = pd.DataFrame(records, columns=EMAIL_THREADS_COLUMNS)
    if not df.empty:
        df["client_id"] = pd.to_numeric(df["client_id"], errors="coerce").astype("Int64")
        df["message_count"] = pd.to_numeric(df["message_count"], errors="coerce").fillna(0).astype(int)
    return df


def upsert_email_threads(threads: list) -> None:
    """
    threads : liste de dicts (clés = tout ou partie de EMAIL_THREADS_COLUMNS),
    UN SEUL dict par thread_id. Met à jour les threads déjà connus et ajoute
    les nouveaux, en UN SEUL appel de lecture + UN SEUL (ou deux : update +
    append) appel d'écriture pour tout le lot — quel que soit le nombre de
    threads touchés dans ce cycle de synchronisation.
    """
    if not threads:
        return
    ws = get_or_create_email_threads_worksheet()
    existing = ws.get_all_values()  # inclut l'en-tête
    data_rows = existing[1:] if existing else []
    row_index_by_thread_id = {row[0]: i for i, row in enumerate(data_rows) if row}

    cell_updates = []
    new_rows = []
    for t in threads:
        thread_id = str(t.get("thread_id", "")).strip()
        if not thread_id:
            continue
        if thread_id in row_index_by_thread_id:
            row_number = row_index_by_thread_id[thread_id] + 2  # +1 en-tête, +1 pour 1-based
            for col_index, col in enumerate(EMAIL_THREADS_COLUMNS, start=1):
                if col in t:  # ne réécrit que les colonnes fournies dans ce dict
                    cell_updates.append(
                        {
                            "range": gspread.utils.rowcol_to_a1(row_number, col_index),
                            "values": [[t[col]]],
                        }
                    )
        else:
            new_rows.append([str(t.get(col, "")) for col in EMAIL_THREADS_COLUMNS])

    if cell_updates:
        ws.batch_update(cell_updates, value_input_option="USER_ENTERED")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")


# ---------------------------------------------------------------------------
# Gmail — messages (EmailMessages)
# ---------------------------------------------------------------------------
def get_or_create_email_messages_worksheet(force_refresh: bool = False) -> gspread.Worksheet:
    now = time.time()
    if (
        not force_refresh
        and _email_messages_ws_cache["ws"] is not None
        and (now - _email_messages_ws_cache["ts"]) < _SHEET_CACHE_TTL_SECONDS
    ):
        return _email_messages_ws_cache["ws"]

    sheet = _get_spreadsheet(force_refresh)
    try:
        ws = sheet.worksheet(EMAIL_MESSAGES_WORKSHEET)
    except gspread.WorksheetNotFound:
        ws = sheet.add_worksheet(
            title=EMAIL_MESSAGES_WORKSHEET, rows=20000, cols=len(EMAIL_MESSAGES_COLUMNS)
        )
        ws.append_row(EMAIL_MESSAGES_COLUMNS)

    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(EMAIL_MESSAGES_COLUMNS)

    _email_messages_ws_cache["ws"] = ws
    _email_messages_ws_cache["ts"] = now
    return ws


def get_existing_message_ids() -> set:
    """Pour la déduplication lors d'une resynchronisation : tous les
    message_id déjà stockés (une seule colonne lue, pas toute la feuille)."""
    ws = get_or_create_email_messages_worksheet()
    col_index = EMAIL_MESSAGES_COLUMNS.index("message_id") + 1
    values = ws.col_values(col_index)
    return set(values[1:])  # on saute l'en-tête


def append_email_messages(messages: list) -> int:
    """Ajoute plusieurs messages en un seul appel à l'API. `messages` doit
    déjà avoir été filtré pour ne contenir AUCUN message_id présent dans
    get_existing_message_ids() — c'est ce qui évite les doublons."""
    if not messages:
        return 0
    ws = get_or_create_email_messages_worksheet()
    rows = [[str(m.get(col, "")) for col in EMAIL_MESSAGES_COLUMNS] for m in messages]
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)


def load_email_messages_for_thread(thread_id: str) -> pd.DataFrame:
    """Charge tous les messages d'un thread donné, triés par date — pour
    affichage ou pour les envoyer à l'IA (classification du stage)."""
    ws = get_or_create_email_messages_worksheet()
    records = ws.get_all_records()
    df = pd.DataFrame(records, columns=EMAIL_MESSAGES_COLUMNS)
    if df.empty:
        return df
    return df[df["thread_id"] == str(thread_id)].sort_values("date")


# ---------------------------------------------------------------------------
# Gmail — lien avec les Clients existants
# ---------------------------------------------------------------------------
def find_or_create_client_for_email(email_address: str, display_name: str = "") -> Optional[int]:
    """
    Cherche un client existant par email (colonne "email" de Clients). Si
    aucun ne correspond, en crée un nouveau automatiquement (import Gmail)
    avec le nom affiché comme contact et le domaine comme nom d'entreprise
    provisoire — à corriger/compléter à la main ensuite si besoin.
    Renvoie l'id du client (existant ou nouvellement créé), ou None si
    email_address est vide.
    """
    email_address = (email_address or "").strip().lower()
    if not email_address:
        return None

    df = load_clients_df()
    if not df.empty:
        match = df[df["email"].astype(str).str.strip().str.lower() == email_address]
        if not match.empty:
            return int(match.iloc[0]["id"])

    domain = email_address.split("@")[-1] if "@" in email_address else email_address
    guessed_company = domain.split(".")[0].capitalize() if domain else email_address
    new_id = append_client(
        {
            "company": guessed_company,
            "contact_name": display_name or email_address,
            "email": email_address,
            "source": "Import Gmail",
            "notes": f"Créé automatiquement depuis Gmail (domaine : {domain}). À compléter/vérifier.",
        }
    )
    return new_id
