"""Validation and identity for a three-email follow-up sequence."""
import hashlib
import json


def context_key(sender_client_key: str, first_email_body: str) -> str:
    revision = hashlib.sha256(first_email_body.strip().encode()).hexdigest()[:16]
    return f"{sender_client_key}:followups:{revision}"


def validate(sequence) -> list:
    if not isinstance(sequence, list) or len(sequence) != 3:
        raise ValueError("La réponse doit contenir exactement 3 follow-ups. Réessaie la génération.")
    cleaned = []
    for item in sequence:
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("subject", "body")):
            raise ValueError("Chaque follow-up doit contenir un objet et un texte. Réessaie la génération.")
        cleaned.append({"subject": item["subject"].strip(), "body": item["body"].strip()})
    return cleaned


def parse(text: str) -> list:
    text = text.strip()
    try:
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return validate(json.loads(text))
    except (json.JSONDecodeError, IndexError) as exc:
        raise ValueError("La réponse des follow-ups est incomplète. Réessaie la génération.") from exc


def validate_first_letter(first_letter: dict) -> dict:
    if not isinstance(first_letter, dict) or not isinstance(first_letter.get("body"), str) or not first_letter["body"].strip():
        raise ValueError("Prépare d'abord le premier email pour donner du contexte aux follow-ups.")
    return {"subject": str(first_letter.get("subject") or "").strip(), "body": first_letter["body"].strip()}
