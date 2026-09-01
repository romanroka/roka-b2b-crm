# -*- coding: utf-8 -*-
"""
Вся настраиваемая логика проекта лежит здесь.
Хочешь поменять статусы, секторы, веса скоринга или тон писем — правь этот файл,
остальной код трогать не нужно.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "ROKA B2B CRM")
CLIENTS_WORKSHEET = "Clients"

# ---------------------------------------------------------------------------
# Claude API (генерация писем)
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
# Отправитель (подпись в письмах)
# ---------------------------------------------------------------------------
SENDER_NAME = os.getenv("SENDER_NAME", "Roman")
SENDER_ROLE = os.getenv("SENDER_ROLE", "Co-fondateur, ROKA")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "roman@roka-shop.com")

# ---------------------------------------------------------------------------
# О бренде — идёт в промпт для генерации писем, чтобы AI не выдумывал
# ---------------------------------------------------------------------------
BRAND_CONTEXT = """
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

OPEN_STATUSES_FOR_RELANCE = ["Contacté", "Relance envoyée"]

# ---------------------------------------------------------------------------
# Справочники полей
# ---------------------------------------------------------------------------
SECTORS = [
    "Hôtel",
    "Concept store",
    "Torréfacteur (roaster)",
    "Café / Coffee shop",
    "Refuge de montagne",
    "Cadeaux d'entreprise (agence)",
    "Entreprise (bureau)",
    "Autre",
]

SOURCES = [
    "LinkedIn",
    "Salon / Festival",
    "Inbound (site/Instagram)",
    "Recommandation",
    "Prospection à froid",
    "Autre",
]

VOLUME_POTENTIAL = ["Faible", "Moyen", "Élevé"]
PRICE_SENSITIVITY = ["Faible", "Moyenne", "Élevée"]  # насколько клиент зациклен на низкой цене

# ---------------------------------------------------------------------------
# Скоринг fit/not fit — веса в сумме дают 100
# ---------------------------------------------------------------------------
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
