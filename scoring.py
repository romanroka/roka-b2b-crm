# -*- coding: utf-8 -*-
"""
Оценка "подходит / не подходит" (fit scoring).

Специально сделано БЕЗ вызова AI: скоринг должен быть прозрачным, воспроизводимым
и бесплатным — считается по понятным правилам из config.py, и всегда можно
объяснить клиенту (или самому себе), почему балл именно такой.

Если критерии нужно поменять — правь веса в config.py, а не эту логику.
"""

from dataclasses import dataclass

import config


@dataclass
class ScoringResult:
    score: int
    label: str
    reasoning: str


def score_client(client: dict) -> ScoringResult:
    sector = (client.get("sector") or "").strip()
    region = (client.get("region") or "").strip()
    volume = (client.get("volume_potential") or "").strip()
    price_sensitivity = (client.get("price_sensitivity") or "").strip()

    sector_pts = config.SECTOR_SCORE.get(sector, 0)
    region_pts = config.REGION_SCORE.get(region, 0)
    volume_pts = config.VOLUME_SCORE.get(volume, 0)
    price_pts = config.PRICE_SENSITIVITY_SCORE.get(price_sensitivity, 0)

    total = sector_pts + region_pts + volume_pts + price_pts

    if total >= config.FIT_THRESHOLD_YES:
        label = "Fit ✅"
    elif total >= config.FIT_THRESHOLD_MAYBE:
        label = "À creuser ⚠️"
    else:
        label = "Pas fit ❌"

    reasoning_parts = []

    if sector:
        reasoning_parts.append(f"Secteur « {sector} » : {sector_pts}/40")
    else:
        reasoning_parts.append("Secteur non renseigné : 0/40")

    if region:
        reasoning_parts.append(f"Zone « {region} » : {region_pts}/20")
    else:
        reasoning_parts.append("Zone non renseignée : 0/20")

    if volume:
        reasoning_parts.append(f"Potentiel de volume « {volume} » : {volume_pts}/25")
    else:
        reasoning_parts.append("Potentiel de volume non renseigné : 0/25")

    if price_sensitivity:
        reasoning_parts.append(
            f"Sensibilité prix « {price_sensitivity} » : {price_pts}/15"
        )
    else:
        reasoning_parts.append("Sensibilité prix non renseignée : 0/15")

    if price_sensitivity == "Élevée":
        reasoning_parts.append(
            "⚠️ Client très sensible au prix — risque de tirer ROKA vers le bas de gamme."
        )

    reasoning = f"Score total : {total}/100.\n" + "\n".join(f"- {p}" for p in reasoning_parts)

    return ScoringResult(score=total, label=label, reasoning=reasoning)
