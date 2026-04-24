"""
servers/filter_server/classifier.py
──────────────────────────────────────
Groq LLM wrapper for ingredient classification.

FIXED from original:
  - import config works now (config/__init__.py added)
  - function renamed to classify_with_groq (exported by server.py)
  - added robust JSON fallback parsing
  - safe/chemical classification rules tightened
"""
from __future__ import annotations
import json
import re
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from groq import Groq

logger  = logging.getLogger(__name__)
_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


def classify_with_groq(ingredients: list, usage: str = "cosmetic") -> dict:
    """
    Classify each ingredient as 'chemical' or 'safe' using Groq LLM.

    safe     = water, aqua, natural plant oils/butters/extracts,
               vitamins, waxes, sugars, minerals as fillers,
               simple emollients like cetyl alcohol, glycerin

    chemical = synthetic preservatives, surfactants, PEGs, parabens,
               formaldehyde releasers, synthetic fragrances, silicones,
               synthetic dyes, UV filters, chelating agents,
               anything with a toxicological concern or CAS number
               When uncertain → chemical (conservative)
               Unknown → chemical + unverified=True

    Args:
        ingredients: list of {name: str} dicts
        usage: product type — cosmetic | food | detergent | pharmaceutical

    Returns:
        {
          "chemicals":    [{name, reason, unverified}],
          "safe_skipped": [{name, reason}],
        }
    """
    if not ingredients:
        return {"chemicals": [], "safe_skipped": []}

    names = "\n".join(
        f"- {i.get('name', '?')}"
        for i in ingredients
    )

    prompt = (
        f"Product type: {usage}\n"
        f"Ingredients:\n{names}\n\n"
        "Classify each as 'chemical' or 'safe'.\n\n"
        "safe = water/aqua, natural oils/butters/extracts, vitamins, waxes,\n"
        "       glycerin, minerals as fillers, simple emollients\n"
        "chemical = synthetic preservatives, surfactants, parabens, PEGs,\n"
        "           fragrances (PARFUM), solvents, dyes, silicones, UV filters\n"
        "uncertain → chemical (conservative)\n"
        "unknown/unrecognised → chemical with unverified=true\n\n"
        "Return ONLY this JSON, no markdown:\n"
        '{"chemicals":[{"name":"...","reason":"...","unverified":false}],'
        '"safe_skipped":[{"name":"...","reason":"..."}]}'
    )

    try:
        resp = _get_client().chat.completions.create(
            model=config.GROQ_MODEL,
            max_tokens=1000,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": "You are a cosmetic chemistry expert. Return only valid JSON, no markdown."
                },
                {"role": "user", "content": prompt},
            ],
        )
        raw   = resp.choices[0].message.content.strip()
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

        try:
            result = json.loads(clean)
        except json.JSONDecodeError:
            # Try to extract JSON object from surrounding text
            m = re.search(r"(\{.*\})", clean, re.DOTALL)
            if m:
                result = json.loads(m.group(1))
            else:
                raise

        # Ensure required keys exist
        if "chemicals" not in result:
            result["chemicals"] = []
        if "safe_skipped" not in result:
            result["safe_skipped"] = []

        return result

    except Exception as e:
        logger.error(f"Groq classification failed: {e}")
        # Conservative fallback: treat everything as chemical
        return {
            "chemicals": [
                {
                    "name":       i.get("name", "?"),
                    "reason":     f"classification failed — conservative fallback",
                    "unverified": True,
                }
                for i in ingredients
            ],
            "safe_skipped": [],
        }