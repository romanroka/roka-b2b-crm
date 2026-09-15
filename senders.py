"""Sender profiles and draft identity, independent of Streamlit sessions."""
import hashlib
import json
import re

import config


def legacy_sender(settings: dict) -> dict:
    """Keep the existing signature available when no users have been saved yet."""
    return {
        "id": "default",
        "name": str(settings.get("sender_name") or config.SENDER_NAME),
        "role": str(settings.get("sender_role", config.SENDER_ROLE)),
        "email": str(settings.get("sender_email", config.SENDER_EMAIL)),
        "context": "",
    }


def validate_sender(profile: dict) -> dict:
    cleaned = {key: str(profile.get(key) or "").strip() for key in ("id", "name", "role", "email", "context")}
    if not cleaned["id"] or not cleaned["name"]:
        raise ValueError("Le nom de l'utilisateur est obligatoire.")
    if any("\n" in cleaned[key] or "\r" in cleaned[key] for key in ("id", "name", "role", "email")):
        raise ValueError("Le nom, la fonction et l'email doivent tenir sur une seule ligne.")
    if cleaned["email"] and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", cleaned["email"]):
        raise ValueError("Indique une adresse email valide, ou laisse ce champ vide.")
    return cleaned


def signature(profile: dict) -> str:
    return "\n".join(str(profile.get(key) or "").strip() for key in ("name", "role", "email") if str(profile.get(key) or "").strip())


def draft_key(profile: dict, client_id: int, brand_context: str) -> str:
    """An edited sender or brand must not expose a draft with the old identity."""
    content = json.dumps({"sender": profile, "brand": brand_context}, sort_keys=True, ensure_ascii=False)
    revision = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"{profile['id']}:{int(client_id)}:{revision}"
