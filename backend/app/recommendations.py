"""Explainable local advice. Never switch accounts, run models or authorize spending."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from .models import Model


class RecommendationPreferences(Model):
    power: Literal["routine", "standard", "complex"] = "standard"
    speed: Literal["conserve", "fast"] = "conserve"
    allow_shared_api: bool = False


def recommend(cards: list[dict], preferences: RecommendationPreferences, now: datetime) -> dict:
    candidates, excluded = [], []

    def capacity(card):
        try:
            left = Decimal(str(card.get("remaining_value")))
            updated = datetime.fromisoformat(card["last_updated"])
            reset = datetime.fromisoformat(card["reset_at"]) if card.get("reset_at") else None
            max_age = timedelta(hours=48) if card["source"] == "manual" else timedelta(minutes=10)
            if not left.is_finite() or left <= 0 or not timedelta(0) <= now - updated <= max_age:
                return None
            return (
                left
                if (reset is None or reset > now) and card["data_status"] == "current"
                else None
            )
        except (InvalidOperation, ValueError, TypeError, KeyError):
            return None

    for card in cards:
        identifier = card["id"]
        if identifier == "api_spending":
            continue  # A monitoring budget does not establish spendable API credit.
        left = capacity(card)
        if left is None:
            excluded.append(
                {
                    "id": identifier,
                    "reason": f"{card['title']}: "
                    "balance is exhausted, unknown, stale or awaiting reset confirmation.",
                }
            )
            continue
        reset = datetime.fromisoformat(card["reset_at"]) if card.get("reset_at") else None
        reasons, cautions = [], []
        if identifier == "codex":
            score = 100 if left > 10 else 35
            route = "Codex with ChatGPT sign-in"
            connection = "In PyCharm Air, select Codex and its ChatGPT / agent login."
            reasons.append(f"The live Codex allowance reports {left:g}% remaining.")
            if left <= 10:
                cautions.append("Codex capacity is low; a large task may reach its limit.")
        elif identifier == "jetbrains" or identifier.startswith("jetbrains:"):
            score = 35 if (card.get("used_percent") or 0) >= 90 else 85
            score += 20 if card.get("preferred") else 0
            route = "JetBrains AI included credits · " + identifier.removeprefix("jetbrains:")
            connection = "In PyCharm, select JetBrains AI authorization in Air or AI Assistant."
            if card["source"] == "live":
                reasons.append(f"Central CLI reports {left:g} included credits remaining.")
                cautions.append("Select the matching licence in PyCharm; reporting can lag.")
            else:
                reasons.append(f"Your recent manual observation records {left:g} included credits.")
                cautions.append("Confirm the manual balance in the IDE before starting.")
            if (card.get("used_percent") or 0) >= 90:
                cautions.append(
                    "Little included JetBrains credit remains; a large task may exhaust it."
                )
            if reset and reset - now <= timedelta(days=3):
                score += 45
                reasons.append(
                    "These included credits reset within three days; use them before reset."
                )
        elif identifier in ("free_large", "free_small"):
            if not preferences.allow_shared_api:
                excluded.append(
                    {
                        "id": identifier,
                        "reason": f"{card['title']}: shared API "
                        "traffic has not been marked suitable for this task.",
                    }
                )
                continue
            large_needed = preferences.power == "complex"
            if (identifier == "free_large") != large_needed:
                excluded.append(
                    {
                        "id": identifier,
                        "reason": f"{card['title']}: the other "
                        "model pool better matches the requested task difficulty.",
                    }
                )
                continue
            score = 80
            route = "OpenAI API · " + (
                "larger-model free pool" if large_needed else "mini/nano free pool"
            )
            connection = "Use an OpenAI API-key connection on a configured shared project "
            connection += "and an eligible model from this pool."
            reasons.append(f"The configured daily pool estimates {left:,.0f} tokens remaining.")
            cautions.append(
                "Estimated free capacity, not a guaranteed free request. Confirm "
                "a positive API balance; overage and tools can incur charges."
            )
            if reset and reset - now <= timedelta(hours=3):
                score += 30
                reasons.append("This daily allowance resets within three hours.")
            if (card.get("used_percent") or 0) >= 90:
                score -= 50
                cautions.append(
                    "Less than 10% of this pool remains; leave room for the whole request."
                )
        else:
            continue

        power = preferences.power
        model_profile = {
            "routine": "A lighter coding model with low reasoning for small edits.",
            "standard": "A balanced coding model with medium reasoning for feature development.",
            "complex": "A strong model with high reasoning for difficult bugs and design.",
        }[power]
        if identifier == "free_small":
            model_profile = (
                "An eligible mini model; prefer nano only for straightforward, narrow tasks."
            )
        elif identifier == "free_large":
            model_profile = (
                "An eligible reasoning-capable model in your configured larger-model pool."
            )
        reasons.append(
            {
                "routine": "A small task can use a lighter model and less reasoning.",
                "standard": "Medium reasoning balances coding capability and allowance use.",
                "complex": "Keep high reasoning for this task; reduce scope to save allowance.",
            }[power]
        )
        speed_profile = "Standard speed to preserve allowance."
        if preferences.speed == "fast":
            if identifier == "codex" and left > 25:
                speed_profile = "Fast mode if available; keep the selected reasoning level."
                cautions.append(
                    "Fast mode consumes more allowance than Standard. Availability "
                    "and multipliers depend on the selected model and plan."
                )
            elif power == "routine":
                speed_profile = "Prefer a lighter model and short prompts for quicker replies."
            else:
                speed_profile = "Use smaller work units; keep the requested reasoning power."
                if identifier == "codex":
                    cautions.append("Standard speed is preferred while Codex allowance is low.")
        candidates.append(
            {
                "route_id": identifier,
                "title": route,
                "score": score,
                "source": card["source"],
                "remaining": card["remaining"],
                "last_updated": card["last_updated"],
                "reset_at": card["reset_at"],
                "model_profile": model_profile,
                "speed_profile": speed_profile,
                "connection": connection,
                "reasons": reasons,
                "cautions": cautions,
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["route_id"]))
    return {
        "generated_at": now.isoformat(),
        "preferences": preferences.model_dump(),
        "status": "recommended" if candidates else "needs_check",
        "choice": candidates[0] if candidates else None,
        "alternatives": candidates[1:],
        "excluded": excluded,
        "message": ""
        if candidates
        else "Refresh usage or record a current balance before "
        "choosing an account. No verified included capacity is available for this task.",
        "basis": "Rule-based guidance, not measured model benchmarks or a guarantee of access. "
        "Choose a model offered in your client. Nothing is switched automatically.",
        "paid_api_note": "API budget remaining is not a prepaid balance; additional paid usage "
        "is not recommended automatically.",
    }
