"""
servers/filter_server/classifier.py
──────────────────────────────────────
Ingredient classifier - uses config.groq.GroqClient
All caching, timeout, retry, and fallback are handled in config.groq
"""

from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.groq import get_groq_client


def classify_with_groq(ingredients: list, usage: str = "cosmetic") -> dict:
    """
    Classify ingredients using the global Groq client.
    All complexity (caching, timeout, retry, fallback) is handled in config.groq.
    """
    if not ingredients:
        return {"chemicals": [], "safe_skipped": [], "unclassified": []}
    
    client = get_groq_client()
    return client.classify_ingredients(ingredients, usage)


# Quick test
if __name__ == "__main__":
    print("=" * 60)
    print("TESTING FILTER SERVER (using config.groq)")
    print("=" * 60)
    
    test_ingredients = [
        {"name": "AQUA"},
        {"name": "WATER"},
        {"name": "GLYCERIN"},
        {"name": "SODIUM LAURETH SULFATE"},
        {"name": "COCO-BETAINE"},
        {"name": "PARFUM"},
        {"name": "PHENOXYETHANOL"},
        {"name": "LIMONENE"},
        {"name": "DIMETHICONE"},
        {"name": "CERAMIDE NP"},
        {"name": "XANTHAN GUM"},
    ]
    
    print(f"\n📋 Testing {len(test_ingredients)} ingredients...")
    
    result = classify_with_groq(test_ingredients, "cosmetic")
    
    print(f"\n✅ RESULTS:")
    print(f"\n  🔬 CHEMICALS ({len(result.get('chemicals', []))}):")
    for c in result.get("chemicals", []):
        unverified = "⚠️ UNVERIFIED" if c.get("unverified") else ""
        print(f"    - {c['name']}: {c['reason']} {unverified}")
    
    print(f"\n  ✅ SAFE SKIPPED ({len(result.get('safe_skipped', []))}):")
    for s in result.get("safe_skipped", []):
        print(f"    - {s['name']}: {s['reason']}")
    
    print("\n" + "=" * 60)
    print("✅ Filter server ready")