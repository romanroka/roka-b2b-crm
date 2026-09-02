# -*- coding: utf-8 -*-
"""
Intégration Gmail : connexion OAuth, import des emails envoyés/reçus, et
détermination du "CRM stage" de chaque conversation par l'IA.

Important sur "l'automatique" : Streamlit Community Cloud (hébergement
gratuit) ne fait pas tourner de processus permanent en tâche de fond — donc
il n'y a pas de synchronisation qui se déclenche toute seule à 3h du matin.
À la place, la synchronisation se déclenche à chaque ouverture de l'appli
(au plus une fois toutes les GMAIL_MIN_SYNC_INTERVAL_MINUTES minutes, pour
ne pas spammer l'API à chaque clic — voir config.py), ou manuellement via le
bouton "🔄 Synchroniser maintenant" dans l'onglet Gmail.

Important sur le compte Google utilisé : c'est un identifiant OAuth
"Application Web" (PAS le compte de service utilisé pour Google Sheets) —
voir GMAIL_SETUP.md pour la configuration complète dans Google Cloud Console.
Tant que cette appli OAuth reste en statut "Test" (le cas normal pour un
usage perso), Google fait expirer le refresh_token au bout de 7 jours : il
suffit alors de se reconnecter une fois via le bouton "Se connecter avec
Google" dans l'onglet Gmail — aucune perte de données, juste une reconnexion.

Fonctions publiques principales :
    get_authorization_url(state)   -> URL vers laquelle rediriger l'utilisateur
    exchange_code_for_tokens(code) -> dict de tokens (à sauver via sheets.save_gmail_auth)
    is_connected()                 -> bool
    should_auto_sync(auth)         -> bool (respecte GMAIL_MIN_SYNC_INTERVAL_MINUTES)
    sync(max_messages=None)        -> dict résumé de la synchronisation
"""

import base64
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

import pandas as pd
import requests

import config
import sheets
from letters import _get_anthropic_client, _extract_text

AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------
def get_authorization_url(state: str = "") -> str:
    params = {
        "client_id": config.GMAIL_CLIENT_ID,
        "redirect_uri": config.GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(config.GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    query = "&".join(f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in params.items())
    return f"{AUTH_BASE_URL}?{query}"


def exchange_code_for_tokens(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": config.GMAIL_CLIENT_ID,
            "client_secret": config.GMAIL_CLIENT_SECRET,
            "redirect_uri": config.GMAIL_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    email_address = _fetch_own_email(data["access_token"])
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "token_expiry": (
            datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
        ).isoformat(),
        "email_address": email_address,
        "last_synced_at": "",
    }


def _fetch_own_email(access_token: str) -> str:
    resp = requests.get(
        f"{GMAIL_API_BASE}/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("emailAddress", "")


def _refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": config.GMAIL_CLIENT_ID,
            "client_secret": config.GMAIL_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "token_expiry": (
            datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600))
        ).isoformat(),
    }


def is_connected() -> bool:
    auth = sheets.load_gmail_auth()
    return bool(auth.get("refresh_token"))


def should_auto_sync(auth: dict) -> bool:
    last_synced_at = auth.get("last_synced_at", "")
    if not last_synced_at:
        return True
    try:
        last = datetime.fromisoformat(last_synced_at)
    except ValueError:
        return True
    return datetime.now(timezone.utc) >= last + timedelta(minutes=config.GMAIL_MIN_SYNC_INTERVAL_MINUTES)


def _get_valid_access_token(auth: dict) -> str:
    """Renvoie un access_token valide, en le rafraîchissant si besoin (et en
    sauvegardant le nouveau token dans la Sheet)."""
    expiry_str = auth.get("token_expiry", "")
    access_token = auth.get("access_token", "")
    needs_refresh = True
    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            needs_refresh = datetime.now(timezone.utc) >= expiry - timedelta(minutes=2)
        except ValueError:
            needs_refresh = True

    if not needs_refresh and access_token:
        return access_token

    refresh_token = auth.get("refresh_token", "")
    if not refresh_token:
        raise RuntimeError(
            "Connexion Gmail expirée et aucun refresh_token disponible — "
            "reconnecte-toi via l'onglet Gmail."
        )
    refreshed = _refresh_access_token(refresh_token)
    updated_auth = {**auth, **refreshed}
    sheets.save_gmail_auth(updated_auth)
    return updated_auth["access_token"]


# ---------------------------------------------------------------------------
# Appels bruts à l'API Gmail (REST, via requests — pas besoin de la lib
# googleapiclient, plus lourde, pour ce qu'on fait ici)
# ---------------------------------------------------------------------------
def _list_message_ids(access_token: str, query: str, max_messages: int) -> list:
    ids = []
    page_token = None
    while len(ids) < max_messages:
        params = {"q": query, "maxResults": min(100, max_messages - len(ids))}
        if page_token:
            params["pageToken"] = page_token
        resp = requests.get(
            f"{GMAIL_API_BASE}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        ids.extend(m["id"] for m in data.get("messages", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids[:max_messages]


def _get_message(access_token: str, message_id: str) -> dict:
    resp = requests.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"format": "full"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _build_raw_message(to_email: str, subject: str, body_text: str, from_email: str = "") -> str:
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["To"] = to_email
    msg["Subject"] = subject
    if from_email:
        sender_name = getattr(config, "SENDER_NAME", "") or ""
        msg["From"] = f"{sender_name} <{from_email}>" if sender_name else from_email
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def send_email(access_token: str, to_email: str, subject: str, body_text: str, from_email: str = "") -> dict:
    """Envoie RÉELLEMENT un email via l'API Gmail (nécessite le scope
    gmail.send — voir config.GMAIL_SCOPES). Renvoie la réponse Gmail (contient
    entre autres l'id et le threadId du message envoyé)."""
    payload = {"raw": _build_raw_message(to_email, subject, body_text, from_email)}
    resp = requests.post(
        f"{GMAIL_API_BASE}/messages/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def send_bulk(items: list, delay_seconds: float = None) -> dict:
    """
    Envoi groupé réel (remplace l'usage qui était fait de Mailmeteor) :
    items = liste de dicts {"client_id", "to_email", "subject", "body",
    "company"}. Chaque email est envoyé pour de vrai via le compte Gmail
    connecté — CE N'EST PAS un brouillon.

    Protections anti-spam intégrées :
    - une pause de delay_seconds (config.GMAIL_SEND_DELAY_SECONDS par défaut)
      entre deux envois, pour ne pas déclencher les limites de débit de Gmail ;
    - un plafond quotidien (config.GMAIL_SEND_DAILY_LIMIT), qui prend en
      compte les envois déjà faits aujourd'hui — au-delà, les emails
      supplémentaires ne sont PAS envoyés (ils reviennent dans "skipped_limit"
      pour être renvoyés le lendemain).

    Renvoie {"sent": [items envoyés], "skipped_limit": [items non envoyés,
    limite atteinte], "failed": [{"item":…, "error":…}]}.
    """
    auth = sheets.load_gmail_auth()
    if not auth.get("refresh_token"):
        raise RuntimeError("Gmail non connecté.")

    access_token = _get_valid_access_token(auth)
    own_email = (auth.get("email_address") or "").strip()

    delay_seconds = config.GMAIL_SEND_DELAY_SECONDS if delay_seconds is None else delay_seconds
    already_sent_today = sheets.get_gmail_send_count_today()
    budget = max(0, config.GMAIL_SEND_DAILY_LIMIT - already_sent_today)

    sent, skipped_limit, failed = [], [], []
    for i, item in enumerate(items):
        if len(sent) >= budget:
            skipped_limit.append(item)
            continue
        to_email = (item.get("to_email") or "").strip()
        if not to_email:
            failed.append({"item": item, "error": "Email destinataire manquant."})
            continue
        try:
            send_email(access_token, to_email, item.get("subject", ""), item.get("body", ""), own_email)
            sent.append(item)
        except Exception as e:
            failed.append({"item": item, "error": str(e)})
        if i < len(items) - 1:
            time.sleep(delay_seconds)

    if sent:
        sheets.increment_gmail_send_count(len(sent))

    return {"sent": sent, "skipped_limit": skipped_limit, "failed": failed}


def _text_to_html(text: str) -> str:
    """Convertit le texte brut d'une lettre en HTML simple (paragraphes +
    retours à la ligne), en échappant tout caractère spécial — nécessaire
    pour envoyer un email HTML (seul moyen d'afficher une image DANS le
    corps du message, un lien mailto ne le permet pas)."""
    escaped = html.escape(text or "")
    paragraphs = [p for p in escaped.split("\n\n") if p.strip()]
    html_paragraphs = ["<p>" + p.replace("\n", "<br>") + "</p>" for p in paragraphs]
    return (
        '<div style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; '
        'color: #222222; line-height: 1.5;">' + "".join(html_paragraphs) + "</div>"
    )


def _build_raw_html_message(
    to_email: str, subject: str, html_body: str, from_email: str = "", inline_image: dict = None
) -> str:
    sender_name = getattr(config, "SENDER_NAME", "") or ""
    from_header = f"{sender_name} <{from_email}>" if (from_email and sender_name) else from_email

    if inline_image:
        msg = MIMEMultipart("related")
        msg["To"] = to_email
        msg["Subject"] = subject
        if from_header:
            msg["From"] = from_header
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        img_bytes = base64.b64decode(inline_image["data_base64"])
        subtype = (inline_image.get("content_type") or "image/jpeg").split("/")[-1]
        image_part = MIMEImage(img_bytes, _subtype=subtype)
        cid = inline_image.get("cid", "roka_image_1")
        image_part.add_header("Content-ID", f"<{cid}>")
        image_part.add_header(
            "Content-Disposition", "inline", filename=inline_image.get("name", "image") + f".{subtype}"
        )
        msg.attach(image_part)
    else:
        msg = MIMEText(html_body, "html", "utf-8")
        msg["To"] = to_email
        msg["Subject"] = subject
        if from_header:
            msg["From"] = from_header

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def send_html_email(
    access_token: str, to_email: str, subject: str, html_body: str, from_email: str = "", inline_image: dict = None
) -> dict:
    """Envoie RÉELLEMENT un email HTML via l'API Gmail, avec une image
    optionnelle affichée DANS le corps du message (pas en pièce jointe)."""
    payload = {"raw": _build_raw_html_message(to_email, subject, html_body, from_email, inline_image)}
    resp = requests.post(
        f"{GMAIL_API_BASE}/messages/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def send_single(to_email: str, subject: str, body_text: str, image: dict = None) -> dict:
    """
    Envoi RÉEL d'un seul email (bouton "📧 Envoyer via Gmail" de l'onglet
    Lettres) : met le texte en forme HTML, y intègre l'image choisie si une
    a été passée (voir sheets.get_image()), gère le rafraîchissement du
    token, et respecte le même plafond quotidien anti-spam que l'envoi
    groupé (config.GMAIL_SEND_DAILY_LIMIT).
    """
    to_email = (to_email or "").strip()
    if not to_email:
        raise RuntimeError("Ce client n'a pas d'email renseigné.")

    auth = sheets.load_gmail_auth()
    if not auth.get("refresh_token"):
        raise RuntimeError("Gmail non connecté — va dans l'onglet 📧 Gmail pour te connecter.")

    if sheets.get_gmail_send_count_today() >= config.GMAIL_SEND_DAILY_LIMIT:
        raise RuntimeError(
            f"Plafond quotidien d'envoi atteint ({config.GMAIL_SEND_DAILY_LIMIT} emails/jour, "
            "protection anti-spam) — réessaie demain."
        )

    access_token = _get_valid_access_token(auth)
    own_email = (auth.get("email_address") or "").strip()

    html_body = _text_to_html(body_text)
    inline_image = None
    if image and image.get("data_base64"):
        cid = "roka_image_1"
        inline_image = {**image, "cid": cid}
        html_body += (
            f'<div style="margin-top:16px;"><img src="cid:{cid}" alt="{html.escape(image.get("name", ""))}" '
            'style="max-width:100%;height:auto;"></div>'
        )

    result = send_html_email(access_token, to_email, subject, html_body, own_email, inline_image)
    sheets.increment_gmail_send_count(1)
    return result


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_base64url(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_body(payload: dict) -> str:
    """Cherche récursivement une partie text/plain ; sinon text/html (nettoyé
    de ses balises). Exposé sans underscore : utile aussi pour des tests."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if mime_type == "text/plain" and body_data:
        return _decode_base64url(body_data)

    parts = payload.get("parts") or []
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return _decode_base64url(part["body"]["data"])
    for part in parts:
        found = extract_body(part)
        if found:
            return found

    if mime_type == "text/html" and body_data:
        return _strip_html(_decode_base64url(body_data))

    return ""


def _clean_client_id(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Classification IA du stage d'un thread
# ---------------------------------------------------------------------------
def _parse_stage_json(text: str) -> dict:
    default = {"stage": "", "reasoning": "", "next_action": "", "next_follow_up_date": ""}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return default
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return default
    if not isinstance(data, dict):
        return default

    stage = str(data.get("stage", "")).strip()
    if stage not in config.GMAIL_CRM_STAGES:
        stage = ""
    return {
        "stage": stage,
        "reasoning": str(data.get("reasoning", "")).strip(),
        "next_action": str(data.get("next_action", "")).strip(),
        "next_follow_up_date": str(data.get("next_follow_up_date", "")).strip(),
    }


def classify_thread(thread_id: str, messages_df: pd.DataFrame = None) -> dict:
    """
    Envoie l'historique complet d'un thread à Claude et lui demande de
    déterminer le stage CRM, la prochaine action concrète, et une date de
    relance suggérée si besoin.
    Renvoie {"stage", "reasoning", "next_action", "next_follow_up_date"}
    (valeurs vides si le thread est introuvable ou si l'IA n'a pas pu répondre).

    Si on classifie PLUSIEURS threads d'affilée (ex: pendant sync()), passer
    messages_df déjà chargé une seule fois (sheets.load_all_email_messages_df())
    pour éviter un appel API de lecture par thread — sinon on épuise vite le
    quota Google Sheets. Sans messages_df, on charge juste ce thread (usage
    ponctuel, ex: affichage dans l'appli).
    """
    if messages_df is None:
        messages_df = sheets.load_email_messages_for_thread(thread_id)
    else:
        messages_df = messages_df[messages_df["thread_id"] == str(thread_id)].sort_values("date")
    if messages_df.empty:
        return {"stage": "", "reasoning": "", "next_action": "", "next_follow_up_date": ""}

    transcript_lines = []
    for _, m in messages_df.iterrows():
        who = "NOUS" if m["direction"] == "out" else "EUX"
        body = str(m.get("body_text", ""))[:1500]
        transcript_lines.append(f"[{m['date']}] {who} — {m.get('subject', '')}\n{body}")
    transcript = "\n\n---\n\n".join(transcript_lines)

    stages_txt = ", ".join(config.GMAIL_CRM_STAGES)
    today = datetime.now().strftime("%Y-%m-%d")

    system_prompt = f"""Tu analyses un fil d'emails de prospection/vente B2B pour déterminer où en est la relation commerciale.

{config.BRAND_CONTEXT}

Stages possibles (choisis EXACTEMENT un de ceux-là, écrit pareil) : {stages_txt}

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, sans balises
markdown, exactement dans ce format :
{{
  "stage": "un des stages ci-dessus",
  "reasoning": "1 phrase expliquant pourquoi ce stage",
  "next_action": "1 phrase : quelle est la prochaine action concrète à faire",
  "next_follow_up_date": "AAAA-MM-JJ, ou vide si aucune relance n'est nécessaire"
}}

Aujourd'hui : {today}. Si le dernier message vient de "NOUS" et date de
plusieurs jours sans réponse, propose une date de relance proche (2 à 5 jours
après aujourd'hui). Si "EUX" a répondu très récemment, laisse un peu de temps
avant de relancer. Ne propose aucune date de relance si le stage est "Pas
intéressé" ou "Conclu"."""

    user_prompt = f"Voici l'historique complet de ce fil d'emails :\n\n{transcript}"

    client_api = _get_anthropic_client()
    message = client_api.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = _extract_text(message)
    return _parse_stage_json(text)


# ---------------------------------------------------------------------------
# Synchronisation complète
# ---------------------------------------------------------------------------
def sync(max_messages: int = None) -> dict:
    """
    Importe les nouveaux messages depuis Gmail (première fois : les
    GMAIL_SYNC_MONTHS derniers mois ; ensuite : depuis la dernière synchro),
    les regroupe par thread, lie/crée les clients correspondants dans la base
    existante, et fait classifier le stage de chaque thread modifié par l'IA.

    Renvoie un résumé : {"new_messages": N, "threads_updated": N, "threads_classified": N}.
    Ne modifie RIEN si Gmail n'est pas connecté (lève une RuntimeError).
    """
    auth = sheets.load_gmail_auth()
    if not auth.get("refresh_token"):
        raise RuntimeError("Gmail non connecté.")

    access_token = _get_valid_access_token(auth)
    own_email = (auth.get("email_address") or "").strip().lower()

    last_synced_at = auth.get("last_synced_at", "")
    since = None
    if last_synced_at:
        try:
            since = datetime.fromisoformat(last_synced_at)
        except ValueError:
            since = None
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=config.GMAIL_SYNC_MONTHS * 30)

    query = f"after:{int(since.timestamp())} -in:chats -in:spam -in:trash"
    max_messages = max_messages or config.GMAIL_MAX_MESSAGES_PER_SYNC

    existing_ids = sheets.get_existing_message_ids()
    message_ids = _list_message_ids(access_token, query, max_messages)
    new_ids = [mid for mid in message_ids if mid not in existing_ids]

    new_messages = []
    threads_seen = {}  # thread_id -> agrégat (contact_email, subject, dates in/out)

    for mid in new_ids:
        try:
            raw = _get_message(access_token, mid)
        except requests.HTTPError:
            continue

        headers = raw.get("payload", {}).get("headers", [])
        from_header = _header(headers, "From")
        to_header = _header(headers, "To")
        subject = _header(headers, "Subject")
        from_name, from_email = parseaddr(from_header)
        _, to_email = parseaddr(to_header)
        from_email = from_email.strip().lower()
        to_email = to_email.strip().lower()

        try:
            date_str = datetime.fromtimestamp(
                int(raw.get("internalDate", "0")) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        body_text = extract_body(raw.get("payload", {}))
        thread_id = raw.get("threadId", mid)
        direction = "out" if from_email == own_email else "in"
        contact_email = to_email if direction == "out" else from_email

        new_messages.append(
            {
                "message_id": mid,
                "thread_id": thread_id,
                "direction": direction,
                "date": date_str,
                "from_email": from_email,
                "to_email": to_email,
                "subject": subject,
                "body_text": body_text[:5000],  # sécurité : évite une ligne gigantesque
            }
        )

        agg = threads_seen.setdefault(
            thread_id, {"contact_email": "", "subject": subject, "dates": [], "last_inbound": "", "last_outbound": ""}
        )
        agg["dates"].append(date_str)
        if direction == "in":
            agg["last_inbound"] = max(agg["last_inbound"], date_str) if agg["last_inbound"] else date_str
            if contact_email:
                agg["contact_email"] = contact_email
        else:
            agg["last_outbound"] = max(agg["last_outbound"], date_str) if agg["last_outbound"] else date_str
            if not agg["contact_email"] and contact_email:
                agg["contact_email"] = contact_email

    if new_messages:
        sheets.append_email_messages(new_messages)

    # --- Liaison aux clients existants -----------------------------------
    # Un seul chargement de la feuille Clients pour TOUS les threads de ce
    # cycle (au lieu d'un find_or_create_client_for_email() par thread, qui
    # relirait toute la feuille à chaque fois et épuiserait vite le quota
    # Google Sheets — c'est ce qui causait l'erreur 429 "Quota exceeded").
    existing_threads_df = sheets.load_email_threads_df()
    clients_df = sheets.load_clients_df()

    thread_rows = []
    pending_new_clients = {}  # email -> index dans thread_rows à corriger ensuite
    for thread_id, agg in threads_seen.items():
        if not existing_threads_df.empty:
            prior = existing_threads_df[existing_threads_df["thread_id"] == thread_id]
        else:
            prior = existing_threads_df
        prior_row = prior.iloc[0].to_dict() if not prior.empty else {}

        client_id = _clean_client_id(prior_row.get("client_id"))
        if client_id is None and agg["contact_email"]:
            client_id = sheets.find_client_id_by_email(agg["contact_email"], clients_df)

        last_inbound = agg["last_inbound"] or prior_row.get("last_inbound_date", "")
        last_outbound = agg["last_outbound"] or prior_row.get("last_outbound_date", "")
        first_date = prior_row.get("first_message_date") or (min(agg["dates"]) if agg["dates"] else "")
        message_count = int(prior_row.get("message_count") or 0) + len(agg["dates"])

        thread_rows.append(
            {
                "thread_id": thread_id,
                "client_id": client_id if client_id is not None else "",
                "contact_email": agg["contact_email"] or prior_row.get("contact_email", ""),
                "subject": prior_row.get("subject") or agg["subject"],
                "first_message_date": first_date,
                "last_inbound_date": last_inbound,
                "last_outbound_date": last_outbound,
                "message_count": message_count,
                "last_synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            }
        )
        if client_id is None and agg["contact_email"]:
            pending_new_clients.setdefault(agg["contact_email"], []).append(len(thread_rows) - 1)

    # Crée en un seul appel batch tous les nouveaux clients nécessaires
    # (un par email distinct rencontré, pas un par thread).
    if pending_new_clients:
        emails_to_create = list(pending_new_clients.keys())
        new_client_dicts = []
        for email in emails_to_create:
            domain = email.split("@")[-1] if "@" in email else email
            guessed_company = domain.split(".")[0].capitalize() if domain else email
            new_client_dicts.append(
                {
                    "company": guessed_company,
                    "contact_name": email,
                    "email": email,
                    "source": "Import Gmail",
                    "notes": f"Créé automatiquement depuis Gmail (domaine : {domain}). À compléter/vérifier.",
                }
            )
        new_ids = sheets.append_clients(new_client_dicts)
        for email, new_id in zip(emails_to_create, new_ids):
            for row_index in pending_new_clients[email]:
                thread_rows[row_index]["client_id"] = new_id

    if thread_rows:
        sheets.upsert_email_threads(thread_rows)

    # --- Classification IA -------------------------------------------------
    # Un seul chargement de TOUS les messages (au lieu d'un
    # load_email_messages_for_thread() par thread), et un seul appel
    # d'écriture groupé pour tous les threads classifiés (au lieu d'un
    # upsert_email_threads() par thread) — même logique anti-429 que ci-dessus.
    all_messages_df = sheets.load_all_email_messages_df()
    classified = 0
    classified_rows = []
    for row in thread_rows:
        try:
            stage_info = classify_thread(row["thread_id"], messages_df=all_messages_df)
            if stage_info["stage"]:
                classified_rows.append(
                    {
                        "thread_id": row["thread_id"],
                        "ai_stage": stage_info["stage"],
                        "ai_stage_reasoning": stage_info["reasoning"],
                        "next_action": stage_info["next_action"],
                        "next_follow_up_date": stage_info["next_follow_up_date"],
                    }
                )
                classified += 1
        except Exception:
            continue

    if classified_rows:
        sheets.upsert_email_threads(classified_rows)

    sheets.save_gmail_auth(
        {
            **auth,
            "access_token": access_token,
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    return {
        "new_messages": len(new_messages),
        "threads_updated": len(thread_rows),
        "threads_classified": classified,
    }
