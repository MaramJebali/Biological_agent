"""Groq LLM configuration for the MCP Agent"""

import os
from groq import Groq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class GroqClient:
    """Groq LLM client wrapper for the agent"""
    
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")
        
        self.client = Groq(api_key=self.api_key)
        
        # Available models on Groq free tier
        self.models = {
            "fast": "gemma2-9b-it",           # Fastest, good for classification
            "reasoning": "mixtral-8x7b-32768", # Best for complex reasoning
            "balanced": "llama-3.3-70b-versatile"  # Balanced option
        }
    
    def get_client(self) -> Groq:
        """Return the raw Groq client"""
        return self.client
    
    def classify_ingredients(self, ingredients: list) -> str:
        """
        Classify ingredients as chemicals vs non-chemicals.
        Used by Filter Server.
        """
        prompt = f"""Classify each ingredient as either "chemical" or "non_chemical".

Non-chemicals: water, aqua, glycerin, oils, butters, waxes, simple salts.
Chemicals: surfactants, preservatives, fragrances, dyes, acids, alcohols.

Ingredients: {ingredients}

Return ONLY JSON: {{"chemicals": [...], "non_chemicals": [...], "unclassified": []}}
"""
        response = self.client.chat.completions.create(
            model=self.models["fast"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024
        )
        return response.choices[0].message.content
    
    def estimate_chemical_risk(self, chemical_name: str) -> str:
        """
        Estimate risk when KG has no data.
        Used by Evaluation Server fallback.
        """
        prompt = f"""You are a chemical safety expert. Estimate the safety risk of this chemical based ONLY on its name.

Chemical: {chemical_name}

Return JSON: {{"estimated_risk": "CRITICAL|HIGH|MODERATE|LOW|SAFE", "confidence": 0.0-1.0, "reasoning": "..."}}
"""
        response = self.client.chat.completions.create(
            model=self.models["reasoning"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=512
        )
        return response.choices[0].message.content
    
    def create_plan(self, products_data: str, tools_description: str) -> str:
        """
        Create investigation plan.
        Used by Orchestration Planner.
        """
        prompt = f"""You are a chemical safety investigator. Create an investigation plan.

Available tools:
{tools_description}

Products to analyze:
{products_data}

Return JSON plan with:
- chemicals_to_investigate: list of {{name, priority, depth, reason}}
- combination_checks: {{organ_overlap, cumulative_presence}}
"""
        response = self.client.chat.completions.create(
            model=self.models["reasoning"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2048
        )
        return response.choices[0].message.content
    
    def synthesize_report(self, results_data: str) -> str:
        """
        Create final safety report.
        Used by Orchestration Synthesizer.
        """
        prompt = f"""You are a chemical safety analyst. Create a final safety report.

Investigation results:
{results_data}

Return JSON: {{"products": [...], "organ_overlaps": [...], "cumulative_risks": [...], "summary": "...", "action_items": [...]}}
"""
        response = self.client.chat.completions.create(
            model=self.models["reasoning"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096
        )
        return response.choices[0].message.content


# Singleton instance for use across the application
_groq_client = None


def get_groq_client() -> GroqClient:
    """Get or create the global Groq client instance"""
    global _groq_client
    if _groq_client is None:
        _groq_client = GroqClient()
    return _groq_client


# Quick test
if __name__ == "__main__":
    print("=" * 60)
    print("TESTING GROQ CLIENT")
    print("=" * 60)
    
    try:
        client = get_groq_client()
        print("✅ Groq client initialized")
        print(f"   API Key present: {bool(client.api_key)}")
        print(f"   Available models: {list(client.models.keys())}")
        
        # Test simple classification
        print("\n📋 Testing classify_ingredients...")
        result = client.classify_ingredients(["WATER", "SLS", "PARFUM"])
        print(f"   Response: {result[:200]}...")
        
        print("\n✅ Groq client ready")
        
    except Exception as e:
        print(f"❌ Error: {e}")