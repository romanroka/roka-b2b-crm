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
    notes = client.get("notes")
    if notes:
        lines.append(f"Notes internes (contexte utile, ne pas recopier tel quel) : {notes}")
    return "\n".join(lines)


def generate_first_letter(client: dict) -> str:
    system_prompt = f"""Tu écris des emails de prospection B2B pour ROKA.

{config.BRAND_CONTEXT}

Règles impératives :
- Écris en français, dans un français naturel et correct.
- Ton chaleureux, humain, curieux — comme un email écrit par une vraie personne,
  pas par un service marketing.
- PAS de ton trop commercial : pas de superlatifs creux, pas de pression, pas
  de "offre limitée dans le temps".
- Personnalise vraiment en t'appuyant sur le secteur, la ville et le contexte
  du destinataire — évite les formules génériques.
- Longueur : 100 à 150 mots maximum.
- Termine par une proposition simple et sans pression (ex: envoyer quelques
  échantillons, ou proposer un court échange de 10 minutes).
- Signe avec le nom, le rôle et l'email fournis, sans les inventer.
- Ne mets pas d'objet d'email, uniquement le corps du message.
- Réponds uniquement avec le texte de l'email, sans commentaire ni balises."""

    user_prompt = f"""Voici les informations sur le prospect à contacter :

{_client_context(client)}

Signature à utiliser :
{config.SENDER_NAME}, {config.SENDER_ROLE}
{config.SENDER_EMAIL}

Écris le premier email de prise de contact."""

    client_api = _get_anthropic_client()
    message = client_api.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=600,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text.strip()


def generate_relance(client: dict) -> str:
    system_prompt = f"""Tu écris des emails de relance B2B pour ROKA.

{config.BRAND_CONTEXT}

Règles impératives :
- Écris en français, ton amical, léger, jamais culpabilisant ("je me permets
  de revenir vers vous" plutôt que "vous n'avez pas répondu").
- Très court : 50 à 80 mots.
- Rappelle en une phrase le sujet du premier email, sans le recopier en entier.
- Propose une porte de sortie simple ("dites-moi si ce n'est pas le bon
  moment, ou si vous préférez que je revienne plus tard").
- Signe avec le nom, le rôle et l'email fournis.
- Ne mets pas d'objet d'email, uniquement le corps du message.
- Réponds uniquement avec le texte de l'email, sans commentaire ni balises."""

    previous_letter = client.get("letter_text") or "(email précédent non disponible)"

    user_prompt = f"""Informations sur le prospect :

{_client_context(client)}

Voici le premier email déjà envoyé, pour référence (ne pas le recopier) :
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
        max_tokens=400,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text.strip()
