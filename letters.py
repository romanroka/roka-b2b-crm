# -*- coding: utf-8 -*-
"""
Генерация текста писем на французском через Claude API.

Два сценария:
    generate_first_letter(client) -> первое письмо клиенту
    generate_relance(client)      -> вежливый реланс через N дней без ответа

Тон и правила бренда живут в config.BRAND_CONTEXT — если хочешь поменять стиль
писем, правь его там, а не промпт внутри этого файла.
"""

import anthropic

import config


def _get_anthropic_client() -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY не задан. Добавь его в .env (см. README, шаг 5)."
        )
    default_headers = None
    if config.ANTHROPIC_WORKSPACE_ID:
        # Некоторые ключи из Claude Console ("identity-linked") требуют явно
        # указывать ID рабочего пространства в заголовке запроса.
        default_headers = {"anthropic-workspace-id": config.ANTHROPIC_WORKSPACE_ID}
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, default_headers=default_headers)


def _client_context(client: dict) -> str:
    """Собирает читаемое описание клиента для промпта."""
    lines = [
        f"Entreprise : {client.get('company', '—')}",
        f"Contact : {client.get('contact_name', '—')} ({client.get('contact_role', '—')})",
        f"Secteur d'activité : {client.get('sector', '—')}",
        f"Ville : {client.get('city', '—')}",
        f"Comment on a trouvé ce contact : {client.get('source', '—')}",
        f"Potentiel de volume estimé : {client.get('volume_potential', '—')}",
    ]
    website = client.get("website")
    if website:
        lines.append(f"Site web : {website}")
    notes = client.get("notes")
    if notes:
        lines.append(f"Notes internes (contexte utile, ne pas recopier tel quel) : {notes}")
    return "\n".join(lines)


def _web_search_tools(max_uses: int) -> list:
    if not config.ENABLE_WEB_SEARCH:
        return []
    return [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}]


def _extract_text(message) -> str:
    """
    Avec la recherche web activée, la réponse peut contenir plusieurs blocs
    (recherche, résultats, texte...) au lieu d'un seul bloc de texte. On
    recolle uniquement les blocs de texte, dans l'ordre.
    """
    parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    return "\n".join(parts).strip()


def _parse_subject_and_body(text: str, fallback_subject: str) -> dict:
    """
    Attend le format demandé dans les prompts ci-dessous :
        SUJET: <objet>
        ---
        <corps du message>
    Si Claude n'a pas respecté ce format (ça arrive), on retombe sur un objet
    générique et on garde tout le texte comme corps — pour ne jamais bloquer
    l'utilisateur sur un problème de parsing.
    """
    lines = text.strip().split("\n")
    if lines and lines[0].strip().upper().startswith("SUJET"):
        subject = lines[0].split(":", 1)[-1].strip().strip('"')
        rest_lines = lines[1:]
        if rest_lines and rest_lines[0].strip() == "---":
            rest_lines = rest_lines[1:]
        body = "\n".join(rest_lines).strip()
        if subject and body:
            return {"subject": subject, "body": body}
    return {"subject": fallback_subject, "body": text.strip()}


def generate_first_letter(client: dict) -> dict:
    """Renvoie {"subject": ..., "body": ...} — les deux personnalisés pour ce
    client précis par Claude en un seul appel."""
    search_instructions = ""
    if config.ENABLE_WEB_SEARCH:
        search_instructions = """
Avant d'écrire, utilise la recherche web pour trouver 1 ou 2 informations
RÉELLES et vérifiables sur cette entreprise précise (site web fourni, actualité
récente, description de l'établissement, avis clients notables, spécialité,
ouverture récente, rénovation, prix, style...). Cherche par le nom de
l'entreprise + la ville, et par le site web s'il est fourni.
Utilise ces informations pour personnaliser vraiment l'email — mentionne un
détail concret et exact, jamais générique.
Si tu ne trouves rien de fiable ou d'exact sur cette entreprise précise,
NE JAMAIS inventer ou deviner un détail : contente-toi alors du secteur et de
la ville, sans faire semblant d'avoir des informations que tu n'as pas."""

    system_prompt = f"""Tu écris des emails de prospection B2B pour ROKA.

{config.BRAND_CONTEXT}
{search_instructions}

Règles impératives :
- Écris en français, dans un français naturel et correct.
- Ton chaleureux, humain, curieux — comme un email écrit par une vraie personne,
  pas par un service marketing.
- PAS de ton trop commercial : pas de superlatifs creux, pas de pression, pas
  de "offre limitée dans le temps".
- Personnalise vraiment en t'appuyant sur le secteur, la ville et le contexte
  du destinataire — évite les formules génériques.
- Longueur du corps : 100 à 150 mots maximum.
- Termine par une proposition simple et sans pression (ex: envoyer quelques
  échantillons, ou proposer un court échange de 10 minutes).
- Signe avec le nom, le rôle et l'email fournis, sans les inventer.
- L'objet de l'email doit être court (moins de 60 caractères), personnalisé
  (mentionne l'entreprise ou son secteur/ville — jamais générique), et donner
  envie d'ouvrir sans être putaclic ni écrit en majuscules.
- Réponds STRICTEMENT dans ce format, rien avant ni après :
SUJET: <objet ici>
---
<corps du message ici, sans le recopier ni le commenter>"""

    user_prompt = f"""Voici les informations sur le prospect à contacter :

{_client_context(client)}

Signature à utiliser :
{config.SENDER_NAME}, {config.SENDER_ROLE}
{config.SENDER_EMAIL}

Écris le premier email de prise de contact."""

    client_api = _get_anthropic_client()
    message = client_api.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1200,
        system=system_prompt,
        tools=_web_search_tools(config.WEB_SEARCH_MAX_USES),
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = _extract_text(message)
    fallback_subject = f"ROKA — café spécialité pour {client.get('company', '')}".strip()
    return _parse_subject_and_body(text, fallback_subject)


def generate_relance(client: dict) -> dict:
    """Renvoie {"subject": ..., "body": ...}."""
    is_after_samples = (client.get("status") or "").strip() == "RDV / Échantillons"

    if is_after_samples:
        context_instructions = """
Contexte précis de cette relance : des ÉCHANTILLONS de café ont déjà été
envoyés à ce prospect (pas seulement un email). Le but de cet email est de
prendre des nouvelles de son avis sur les échantillons reçus — est-ce qu'ils
lui sont bien parvenus, qu'en a-t-il pensé, serait-il partant pour la suite
(un appel, une première commande...). Ne parle pas d'un "email resté sans
réponse" — parle des échantillons."""
    else:
        context_instructions = """
Contexte précis de cette relance : un email de prise de contact a été envoyé
et est resté sans réponse. Reviens vers le prospect sans le culpabiliser."""

    system_prompt = f"""Tu écris des emails de relance B2B pour ROKA.

{config.BRAND_CONTEXT}
{context_instructions}

Règles impératives :
- Écris en français, ton amical, léger, jamais culpabilisant ("je me permets
  de revenir vers vous" plutôt que "vous n'avez pas répondu").
- Très court : 50 à 80 mots.
- Rappelle en une phrase le sujet du message précédent, sans le recopier en entier.
- Propose une porte de sortie simple ("dites-moi si ce n'est pas le bon
  moment, ou si vous préférez que je revienne plus tard").
- Signe avec le nom, le rôle et l'email fournis.
- L'objet doit être court (moins de 60 caractères) et personnalisé (mentionne
  l'entreprise), différent de l'objet du premier message si possible.
- Réponds STRICTEMENT dans ce format, rien avant ni après :
SUJET: <objet ici>
---
<corps du message ici, sans le recopier ni le commenter>"""

    previous_letter = client.get("letter_text") or "(message précédent non disponible)"

    user_prompt = f"""Informations sur le prospect :

{_client_context(client)}

Voici le dernier message déjà envoyé, pour référence (ne pas le recopier) :
---
{previous_letter}
---

Signature à utiliser :
{config.SENDER_NAME}, {config.SENDER_ROLE}
{config.SENDER_EMAIL}

Écris un email de relance."""

    client_api = _get_anthropic_client()
    message = client_api.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=800,
        system=system_prompt,
        tools=_web_search_tools(max_uses=1),
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = _extract_text(message)
    fallback_subject = f"ROKA — un petit mot de plus, {client.get('company', '')}".strip()
    return _parse_subject_and_body(text, fallback_subject)
