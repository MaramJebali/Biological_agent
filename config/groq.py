"""Groq LLM configuration - PRODUCTION VERSION
Changes:
- temperature = 0.0 (deterministic)
- Validation: LLM cannot invent chemicals
- LRU caching for repeated queries
- Retry logic with exponential backoff
"""

import os
import json
import re
import time
from functools import lru_cache, wraps
from typing import Dict, List, Optional, Any
from groq import Groq, APIError, RateLimitError, APIConnectionError
from dotenv import load_dotenv

load_dotenv()


def retry_with_backoff(max_retries=3, initial_delay=1, backoff_factor=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (RateLimitError, APIConnectionError) as e:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
                    delay *= backoff_factor
                except APIError as e:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
                    delay *= backoff_factor
            return None
        return wrapper
    return decorator


class GroqClient:
    """Groq LLM client wrapper - SINGLE instance for all servers"""
    
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY not found")
        
        self.models = {
            "fast": "llama-3.1-8b-instant",
            "reasoning": "mixtral-8x7b-32768",
            "balanced": "llama-3.3-70b-versatile"
        }
        
        self.timeout = 30.0
        self._init_client()
    
    def _init_client(self):
        self.client = Groq(
            api_key=self.api_key,
            max_retries=2,
            timeout=self.timeout
        )
    
    @retry_with_backoff(max_retries=3, initial_delay=1, backoff_factor=2)
    def _make_request(self, model: str, messages: list, temperature: float, max_tokens: int):
        return self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
    
    CLASSIFICATION_SYSTEM_PROMPT = """You are an expert cosmetic chemist. Classify ingredients.

SAFE ingredients (skip):
- WATER: aqua, water, eau
- HUMECTANTS: glycerin, glycerol, propanediol, butylene glycol, sorbitol, sodium hyaluronate
- FATTY ALCOHOLS: cetyl alcohol, stearyl alcohol, cetearyl alcohol
- NATURAL OILS/BUTTERS: any oil, any butter, shea, cocoa, jojoba, coconut
- PLANT EXTRACTS: any ingredient containing "extract"
- VITAMINS: tocopherol, ascorbic acid, panthenol
- CERAMIDES: ceramide np, ceramide ap, ceramide eop
- POLYMERS: xanthan gum, carbomer, cellulose
- SALTS: sodium chloride, potassium phosphate

CHEMICAL ingredients (investigate):
- UV FILTERS: homosalate, octisalate, octocrylene, avobenzone, titanium dioxide
- PRESERVATIVES: phenoxyethanol, ethylhexylglycerin, caprylyl glycol, sodium benzoate, benzyl alcohol
- SURFACTANTS: sodium laureth sulfate, sodium lauryl sulfate, coco-betaine, polysorbate
- FRAGRANCES: parfum, fragrance, limonene, linalool, hexyl cinnamal
- CHELATORS: disodium EDTA
- SILICONES: dimethicone
- PEG COMPOUNDS: any ingredient containing "PEG-"

CRITICAL RULES:
- When uncertain, classify as CHEMICAL
- If unrecognizable → chemical with unverified=true

Return ONLY valid JSON:
{
  "chemicals": [{"name": "...", "reason": "...", "unverified": false}],
  "safe_skipped": [{"name": "...", "reason": "..."}]
}"""

    @lru_cache(maxsize=500)
    def _classify_ingredients_batch_cached(self, ingredients_tuple: tuple, usage: str) -> dict:
        ingredients = [{"name": name} for name in ingredients_tuple]
        return self._classify_ingredients_batch_uncached(ingredients, usage)
    
    def _classify_ingredients_batch_uncached(self, ingredients: list, usage: str) -> dict:
        names = "\n".join(f"- {i.get('name', '?')}" for i in ingredients)
        
        user_prompt = f"""Product type: {usage}

Ingredients to classify:
{names}

Return ONLY the JSON."""
        
        try:
            resp = self._make_request(
                model=self.models["fast"],
                messages=[
                    {"role": "system", "content": self.CLASSIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,  # DETERMINISTIC
                max_tokens=2500
            )
            raw = resp.choices[0].message.content.strip()
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            result = json.loads(clean)
            result.setdefault("chemicals", [])
            result.setdefault("safe_skipped", [])
            return result
        except Exception as e:
            print(f"Classification error: {e}")
            return self._fallback_classification(ingredients)
    
    def _fallback_classification(self, ingredients: list) -> dict:
        """Rule-based fallback when LLM fails"""
        chemicals = []
        safe_skipped = []
        
        safe_patterns = ["AQUA", "WATER", "GLYCERIN", "CETEARYL ALCOHOL", "CETYL ALCOHOL",
                         "XANTHAN GUM", "CARBOMER", "CERAMIDE", "SODIUM CHLORIDE"]
        
        chemical_patterns = ["SULFATE", "BETAINE", "PARFUM", "FRAGRANCE", "LIMONENE",
                             "LINALOOL", "EDTA", "DIMETHICONE", "PEG-", "PHENOXYETHANOL"]
        
        for ing in ingredients:
            name = ing.get("name", "")
            name_upper = name.upper()
            
            if any(p in name_upper for p in safe_patterns):
                safe_skipped.append({"name": name, "reason": "Fallback: matches safe pattern"})
            elif any(p in name_upper for p in chemical_patterns):
                chemicals.append({"name": name, "reason": "Fallback: matches chemical pattern", "unverified": False})
            else:
                chemicals.append({"name": name, "reason": "Fallback: unknown - conservative", "unverified": True})
        
        return {"chemicals": chemicals, "safe_skipped": safe_skipped}
    
    def classify_ingredients(self, ingredients: list, usage: str = "cosmetic") -> dict:
        """
        Classify ingredients with CRITICAL validation.
        Ensures LLM doesn't invent chemicals.
        """
        if not ingredients:
            return {"chemicals": [], "safe_skipped": []}
        
        # Build set of original ingredient names for validation
        original_names_set = set()
        original_names_map = {}
        for ing in ingredients:
            name = ing.get("name", "").strip()
            if name:
                name_upper = name.upper()
                original_names_set.add(name_upper)
                original_names_map[name_upper] = name
        
        unique_names = list(original_names_map.keys())
        
        all_chemicals = []
        all_safe = []
        
        batch_size = 20
        for i in range(0, len(unique_names), batch_size):
            batch = unique_names[i:i+batch_size]
            result = self._classify_ingredients_batch_cached(tuple(batch), usage)
            
            # CRITICAL: Validate results against original input
            for c in result.get("chemicals", []):
                chem_name = c.get("name", "").upper()
                if chem_name in original_names_set:
                    c["name"] = original_names_map[chem_name]
                    all_chemicals.append(c)
                else:
                    print(f"WARNING: LLM invented '{c.get('name')}' - ignoring")
            
            for s in result.get("safe_skipped", []):
                safe_name = s.get("name", "").upper()
                if safe_name in original_names_set:
                    s["name"] = original_names_map[safe_name]
                    all_safe.append(s)
                else:
                    print(f"WARNING: LLM invented '{s.get('name')}' - ignoring")
        
        # Add any missing ingredients (not classified by LLM)
        classified_names = {c["name"].upper() for c in all_chemicals} | {s["name"].upper() for s in all_safe}
        for missing in original_names_set - classified_names:
            all_chemicals.append({
                "name": original_names_map[missing],
                "reason": "Not classified by LLM - treating as chemical",
                "unverified": True
            })
        
        return {"chemicals": all_chemicals, "safe_skipped": all_safe}
    
    @lru_cache(maxsize=200)
    def estimate_chemical_risk_cached(self, chemical_name: str) -> dict:
        return self._estimate_chemical_risk_uncached(chemical_name)
    
    def _estimate_chemical_risk_uncached(self, chemical_name: str) -> dict:
        prompt = f"""Chemical: {chemical_name}

Estimate safety risk.

Return JSON: {{"risk": "CRITICAL|HIGH|MODERATE|LOW|SAFE|UNKNOWN", "confidence": 0.0-1.0, "reasoning": "..."}}"""
        
        try:
            resp = self._make_request(
                model=self.models["fast"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300
            )
            raw = resp.choices[0].message.content.strip()
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            return json.loads(clean)
        except Exception as e:
            return {"risk": "UNKNOWN", "confidence": 0.2, "reasoning": f"Error: {e}"}
    
    def estimate_chemical_risk(self, chemical_name: str) -> dict:
        return self.estimate_chemical_risk_cached(chemical_name)
    
    def estimate_organs_cached(self, chemical_name: str, hazard_codes_tuple: tuple) -> dict:
        return self._estimate_organs_uncached(chemical_name, list(hazard_codes_tuple))
    
    def _estimate_organs_uncached(self, chemical_name: str, hazard_codes: list) -> dict:
        prompt = f"""Chemical: {chemical_name}
Hazard codes: {hazard_codes if hazard_codes else "None"}

Estimate target organs.

Return JSON: {{"organs": ["skin", "liver", ...], "confidence": 0.0-1.0, "reasoning": "..."}}"""
        
        try:
            resp = self._make_request(
                model=self.models["fast"],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200
            )
            raw = resp.choices[0].message.content.strip()
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            return json.loads(clean)
        except Exception as e:
            return {"organs": [], "confidence": 0.2, "reasoning": f"Error: {e}"}
    
    def estimate_organs(self, chemical_name: str, hazard_codes: list) -> dict:
        return self.estimate_organs_cached(chemical_name, tuple(hazard_codes))


_groq_client = None


def get_groq_client() -> GroqClient:
    global _groq_client
    if _groq_client is None:
        _groq_client = GroqClient()
    return _groq_client


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING GROQ CLIENT")
    print("=" * 60)
    
    client = get_groq_client()
    print("✅ Groq client initialized")
    
    result = client.classify_ingredients([
        {"name": "AQUA"},
        {"name": "WATER"},
        {"name": "GLYCERIN"},
        {"name": "SODIUM LAURETH SULFATE"},
    ], "cosmetic")
    
    print(f"Chemicals: {len(result.get('chemicals', []))}")
    print(f"Safe: {len(result.get('safe_skipped', []))}")
    print("✅ Groq client ready")