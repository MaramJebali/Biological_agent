"""Configuration module for the MCP Agent"""

import os
from dotenv import load_dotenv

load_dotenv()


def validate() -> None:
    """Validate that all required environment variables are set"""
    missing = []
    
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        missing.append("GROQ_API_KEY")
    
    neo4j_uri = os.getenv("NEO4J_URI")
    if not neo4j_uri:
        missing.append("NEO4J_URI")
    
    neo4j_user = os.getenv("NEO4J_USER")
    if not neo4j_user:
        missing.append("NEO4J_USER")
    
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    if not neo4j_password:
        missing.append("NEO4J_PASSWORD")
    
    if missing:
        raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")
    
    print(f"✅ Configuration validated")
    print(f"   Neo4j URI: {neo4j_uri}")
    print(f"   Groq API key: {'✓ Present' if groq_key else '✗ Missing'}")


# Configuration constants
GROQ_MODEL = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")  # ← ADD THIS LINE
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")