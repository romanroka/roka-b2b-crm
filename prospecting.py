# -*- coding: utf-8 -*-
"""
Recherche automatique de prospects par ville, via Claude + recherche web.

L'IA ne fait QUE proposer une liste de candidats — rien n'est jamais ajouté
à la Google Sheet automatiquement. C'est app.py (onglet "Prospection") qui
affiche les résultats pour validation, et n'ajoute que ce que l'utilisateur
a coché.

Important sur la fiabilité : le nom de l'entreprise et le site web sont
généralement fiables (trouvés par recherche web réelle). L'email et le
téléphone le sont beaucoup moins — beaucoup de petites entreprises n'ont pas
ces infos facilement trouvables en ligne, et l'IA peut se tromper. Ils sont
donc à vérifier avant d'envoyer quoi que ce soit (l'UI le rappelle).
"""

import json

import config
from letters import _get_anthropic_client, _web_search_tools, _extract_text

PROSPECT_FIELDS = ["company", "sector", "city", "website", "email", "phone", "notes"]


def _sectors_for_search() -> list:
    return [s for s in config.SECTORS if s != "Autre"] or list(config.SECTORS)


def _extract_json_array(text: str) -> list:
    """Extrait un tableau JSON de la réponse de Claude, même s'il a ajouté
    du texte ou des balises markdown autour."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def find_prospects(city: str, max_results: int = 10) -> list:
    """
    Cherche sur le web de VRAIES entreprises à `city` correspondant aux
    secteurs cibles (config.SECTORS), et renvoie une liste de dicts avec les
    clés PROSPECT_FIELDS — prête à être affichée pour validation avant ajout.
    """
    city = (city or "").strip()
    if not city:
        return []

    max_results = max(1, min(int(max_results), 20))
    sectors_txt = ", ".join(_sectors_for_search())

    system_prompt = f"""Tu es un assistant de prospection B2B pour cette entreprise :

{config.BRAND_CONTEXT}

Ta tâche : utiliser la recherche web pour trouver de VRAIES entreprises
existantes à {city} (ou dans ses environs proches si peu de résultats sur
place), qui correspondent à l'un de ces secteurs cibles : {sectors_txt}.

Règles impératives :
- N'invente JAMAIS une entreprise, un site web, un email ou un téléphone.
  Si tu n'es pas raisonnablement sûr qu'une information précise est exacte,
  laisse le champ vide ("") plutôt que de deviner ou d'approximer.
- Pour chaque entreprise, cherche si possible un email de contact ou un
  téléphone public (souvent sur leur site, page "Contact" ou "À propos").
  Si tu n'en trouves pas avec un minimum de certitude, laisse vide — ne
  propose jamais un email générique deviné (ex: contact@nomdedomaine.com)
  sans l'avoir vu affiché quelque part.
- Exclut les grandes chaînes / franchises internationales — privilégie les
  établissements indépendants ou petites chaînes locales, plus pertinents
  pour un petit fournisseur spécialisé.
- Ne propose jamais deux fois la même entreprise.
- Maximum {max_results} entreprises. Si tu en trouves moins de qualité
  suffisante, renvoie-en moins plutôt que d'inventer pour compléter.

Réponds UNIQUEMENT avec un tableau JSON valide, sans texte autour, sans
balises markdown, exactement dans ce format :
[
  {{
    "company": "Nom de l'entreprise",
    "sector": "un des secteurs cibles listés ci-dessus, écrit exactement pareil",
    "city": "ville réelle de l'entreprise",
    "website": "URL du site ou vide",
    "email": "email de contact trouvé ou vide",
    "phone": "téléphone trouvé ou vide",
    "notes": "1 phrase expliquant pourquoi c'est un bon prospect et d'où vient l'info (ex: site officiel, avis Google...)"
  }}
]"""

    user_prompt = f"Trouve jusqu'à {max_results} prospects réels à {city}."

    client_api = _get_anthropic_client()
    message = client_api.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4000,
        system=system_prompt,
        tools=_web_search_tools(max_uses=max(8, max_results)),
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = _extract_text(message)
    raw_items = _extract_json_array(text)

    prospects = []
    seen_names = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        company = str(item.get("company") or "").strip()
        key = company.lower()
        if not company or key in seen_names:
            continue
        seen_names.add(key)

        sector = str(item.get("sector") or "").strip()
        if sector not in config.SECTORS:
            sector = "Autre"

        prospects.append(
            {
                "company": company,
                "sector": sector,
                "city": str(item.get("city") or city).strip(),
                "website": str(item.get("website") or "").strip(),
                "email": str(item.get("email") or "").strip(),
                "phone": str(item.get("phone") or "").strip(),
                "notes": str(item.get("notes") or "").strip(),
            }
        )
        if len(prospects) >= max_results:
            break

    return prospects
