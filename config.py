# -*- coding: utf-8 -*-
"""
Вся настраиваемая логика проекта лежит здесь.

ВАЖНО про белый лейбл (продажа под другого клиента):
Всё, что нужно поменять под нового клиента, читается из переменных окружения /
Streamlit Secrets (см. secrets_template.toml) — раздел "НАСТРОЙКИ КЛИЕНТА" ниже.
Для нового клиента НЕ нужно трогать этот файл и вообще какой-либо код — только
заполнить Secrets при деплое новой копии приложения на Streamlit Cloud.
Раздел "ПРОДВИНУТОЕ" ниже (статусы, веса скоринга) можно менять в коде, если
для конкретного клиента нужна более тонкая настройка — но это редко нужно.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _list_from_env(var_name: str, default_list: list) -> list:
    """Читает список из переменной окружения вида 'A, B, C'. Если пусто — дефолт."""
    raw = os.getenv(var_name, "")
    if not raw.strip():
        return default_list
    return [item.strip() for item in raw.split(",") if item.strip()]


# ===========================================================================
# НАСТРОЙКИ КЛИЕНТА — заполняются заново для каждого нового клиента (в Secrets)
# ===========================================================================

# --- Название приложения (шапка страницы и вкладка браузера) ---
APP_TITLE = os.getenv("APP_TITLE", "ROKA — B2B CRM (MVP)")
APP_ICON = os.getenv("APP_ICON", "☕")

# --- Google Sheets: своя таблица на каждого клиента ---
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "ROKA B2B CRM")
CLIENTS_WORKSHEET = "Clients"

# --- Отправитель (подпись в письмах) ---
SENDER_NAME = os.getenv("SENDER_NAME", "Roman")
SENDER_ROLE = os.getenv("SENDER_ROLE", "Co-fondateur, ROKA")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "roman@roka-shop.com")

# --- О бренде клиента — идёт в промпт для генерации писем, чтобы AI не выдумывал ---
# Опиши в свободной форме: что продаём, кому, какой тон, чего избегать.
BRAND_CONTEXT = os.getenv("BRAND_CONTEXT", "").strip() or """
ROKA — французский бренд спешелти кофе в формате портативных drip-bag (дрип-пакетов).
Слоган: "Café libre. Partout." (кофе свободен, повсюду).
Позиционирование: качественный, но не пафосный кофе для тех, кто в движении —
кафе, концепт-стор, отель, горный приют, ростерская, офис.
Важно: НЕ демпинговать и не звучать как дешёвый опт — мы про качество и историю,
а не про самую низкую цену на рынке.
Тон переписки: дружелюбный, живой, по-человечески, без канцелярита и без
агрессивных продажных клише ("уникальное предложение", "не упустите шанс" и т.п.).
Короткие письма лучше длинных.
"""

# --- Секторы и источники лидов — свои под индустрию клиента ---
# В Secrets задаются простой строкой через запятую, например:
# SECTORS = "Hôtel, Restaurant, Boutique, Salle de sport, Autre"
SECTORS = _list_from_env("SECTORS", [
    "Hôtel",
    "Concept store",
    "Torréfacteur (roaster)",
    "Café / Coffee shop",
    "Refuge de montagne",
    "Cadeaux d'entreprise (agence)",
    "Entreprise (bureau)",
    "Autre",
])

SOURCES = _list_from_env("SOURCES", [
    "LinkedIn",
    "Salon / Festival",
    "Inbound (site/Instagram)",
    "Recommandation",
    "Prospection à froid",
    "Autre",
])

# ---------------------------------------------------------------------------
# Claude API (генерация писем) — обычно ОДИН и тот же ключ для всех клиентов,
# т.к. ты платишь за ИИ централизованно.
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Нужен только для новых "identity-linked" ключей Claude Console, которые
# требуют явно указывать ID рабочего пространства (workspace). Если твой
# ключ работает без этого — просто оставь пустым.
ANTHROPIC_WORKSPACE_ID = os.getenv("ANTHROPIC_WORKSPACE_ID", "")
# Если модель устарела/переименована — поменяй здесь.
# Актуальный список: https://docs.claude.com/en/docs/about-claude/models
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

# ---------------------------------------------------------------------------
# Воронка статусов клиента
# ---------------------------------------------------------------------------
STATUSES = [
    "Nouveau",              # новый лид, ещё не оценён
    "À qualifier",          # ждёт скоринга
    "À contacter",          # оценён, fit, письмо ещё не отправлено
    "Contacté",             # первое письмо отправлено
    "Relance envoyée",      # отправлен хотя бы один реланс
    "Répondu",              # клиент ответил
    "RDV / Échantillons",   # назначена встреча или отправлены образцы
    "Client",               # стал клиентом
    "Pas intéressé",        # отказался
    "Non pertinent",        # не fit, не тратим время
]

OPEN_STATUSES_FOR_RELANCE = ["Contacté", "Relance envoyée", "RDV / Échantillons"]

# ---------------------------------------------------------------------------
# Справочники полей, не зависящие от клиента
# ---------------------------------------------------------------------------
VOLUME_POTENTIAL = ["Faible", "Moyen", "Élevé"]
PRICE_SENSITIVITY = ["Faible", "Moyenne", "Élevée"]  # насколько клиент зациклен на низкой цене

# ---------------------------------------------------------------------------
# ПРОДВИНУТОЕ: скоринг fit/not fit — веса в сумме дают 100.
# Веса по секторам оставлены как есть для ROKA (подобраны вручную под нашу
# аудиторию). Если для другого клиента нужны свои веса по секторам — правь
# этот словарь.
# ---------------------------------------------------------------------------
SECTOR_SCORE_OTHER = 10  # балл по умолчанию для секторов, не перечисленных ниже
SECTOR_SCORE = {
    "Hôtel": 40,
    "Concept store": 40,
    "Torréfacteur (roaster)": 40,
    "Refuge de montagne": 35,
    "Cadeaux d'entreprise (agence)": 35,
    "Café / Coffee shop": 25,
    "Entreprise (bureau)": 20,
    "Autre": 10,
}

REGION_SCORE = {
    "France": 20,
    "Europe": 10,
    "Hors Europe": 0,
}

VOLUME_SCORE = {
    "Élevé": 25,
    "Moyen": 15,
    "Faible": 5,
}

# Чем выше зацикленность клиента на низкой цене, тем ниже балл —
# ROKA не хочет становиться "дешёвым" поставщиком (см. фидбек с рынка).
PRICE_SENSITIVITY_SCORE = {
    "Faible": 15,
    "Moyenne": 8,
    "Élevée": 0,
}

FIT_THRESHOLD_YES = 70   # >= это значение -> "Fit"
FIT_THRESHOLD_MAYBE = 40  # >= это, но < YES -> "À creuser" (промежуточный)

# ---------------------------------------------------------------------------
# Реланс
# ---------------------------------------------------------------------------
RELANCE_DELAY_DAYS = int(os.getenv("RELANCE_DELAY_DAYS", "4"))
# Отдельная задержка для клиентов, которым отправили пробники (échantillons) —
# обычно нужно чуть больше времени, чем на обычное письмо, чтобы попробовать
# кофе и составить мнение.
SAMPLE_RELANCE_DELAY_DAYS = int(os.getenv("SAMPLE_RELANCE_DELAY_DAYS", "5"))

# ---------------------------------------------------------------------------
# Колонки листа "Clients" — порядок задаёт порядок столбцов в Google Sheet.
# Если добавляешь новое поле — допиши его в конец, чтобы не сломать индексы
# у уже существующих строк.
# ---------------------------------------------------------------------------
CLIENT_COLUMNS = [
    "id",
    "date_added",
    "company",
    "contact_name",
    "contact_role",
    "email",
    "phone",
    "sector",
    "city",
    "region",          # France / Europe / Hors Europe
    "source",
    "volume_potential",
    "price_sensitivity",
    "notes",
    "status",
    "fit_score",
    "fit_label",
    "fit_reasoning",
    "letter_text",
    "letter_generated_at",
    "last_contact_date",
    "next_relance_date",
    "relance_count",
    "website",  # optionnel — aide Claude à trouver la bonne entreprise en cherchant sur le web
]

# ---------------------------------------------------------------------------
# Recherche web pendant la génération des lettres
# ---------------------------------------------------------------------------
# Si activé, Claude peut chercher sur internet des infos réelles sur l'entreprise
# (actualités, description, avis...) avant d'écrire — pour une lettre bien plus
# personnalisée. Coûte un peu plus cher par lettre (recherches web facturées en plus)
# et prend quelques secondes de plus. Mets à False pour revenir au mode rapide/gratuit.
ENABLE_WEB_SEARCH = os.getenv("ENABLE_WEB_SEARCH", "true").lower() in ("1", "true", "yes")
WEB_SEARCH_MAX_USES = int(os.getenv("WEB_SEARCH_MAX_USES", "3"))

# ---------------------------------------------------------------------------
# Gmail (OAuth) — import automatique des emails envoyés/reçus pour créer et
# tenir à jour le CRM à partir de la vraie correspondance.
#
# À créer une seule fois dans Google Cloud Console (voir GMAIL_SETUP.md) :
# un identifiant OAuth "Application Web" (PAS un compte de service — celui-là
# sert uniquement pour Google Sheets). Il faut y déclarer GMAIL_REDIRECT_URI
# comme "URI de redirection autorisé", exactement égal à l'URL de l'appli
# déployée + "/" (ex: https://mon-app.streamlit.app/).
# ---------------------------------------------------------------------------
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
GMAIL_REDIRECT_URI = os.getenv("GMAIL_REDIRECT_URI", "")
# gmail.send permet l'envoi groupé depuis l'onglet Lettres (voir plus bas).
# Si Gmail a été connecté AVANT l'ajout de ce scope, il faut se déconnecter
# puis se reconnecter une fois pour que Google redemande cette autorisation —
# l'ancien refresh_token ne l'inclut pas automatiquement.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# Combien de mois d'historique importer lors de la toute première synchronisation.
GMAIL_SYNC_MONTHS = int(os.getenv("GMAIL_SYNC_MONTHS", "12"))
# Ne re-synchronise pas plus souvent que ça (en minutes), même si l'appli est
# rouverte en boucle — évite de solliciter l'API Gmail et Claude inutilement.
GMAIL_MIN_SYNC_INTERVAL_MINUTES = int(os.getenv("GMAIL_MIN_SYNC_INTERVAL_MINUTES", "15"))
# Nombre max de messages récupérés en un seul cycle de synchronisation (pagine
# au-delà si besoin, mais évite un premier import monstre en un seul clic).
GMAIL_MAX_MESSAGES_PER_SYNC = int(os.getenv("GMAIL_MAX_MESSAGES_PER_SYNC", "500"))

# --- Envoi groupé ("remplace Mailmeteor") ---------------------------------
# Pause entre deux envois (secondes) : évite de déclencher les protections
# anti-spam de Gmail en envoyant trop de messages d'un coup, trop vite.
GMAIL_SEND_DELAY_SECONDS = float(os.getenv("GMAIL_SEND_DELAY_SECONDS", "2"))
# Plafond d'emails envoyés PAR JOUR par l'appli (compteur remis à zéro chaque
# jour). Volontairement bien en dessous de la limite Gmail perso (~500/jour)
# pour garder de la marge pour tes propres emails manuels et ne pas risquer
# de faire flaguer le compte comme spam.
GMAIL_SEND_DAILY_LIMIT = int(os.getenv("GMAIL_SEND_DAILY_LIMIT", "80"))

# Étapes possibles du "CRM stage" déterminé par l'IA à partir de la
# correspondance (volontairement distinct de STATUSES : c'est un diagnostic
# automatique, pas le statut que tu pilotes toi-même dans l'onglet Clients).
GMAIL_CRM_STAGES = [
    "Nouveau contact",
    "En discussion",
    "En attente de réponse (à relancer)",
    "Intéressé",
    "Pas intéressé",
    "Conclu",
]
