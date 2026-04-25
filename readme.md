# 🧪 Biological Agent

**AI-powered multi-product chemical safety analysis using MCP (Model Context Protocol)**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io)
[![Neo4j](https://img.shields.io/badge/Neo4j-5.x-008CC1.svg)](https://neo4j.com/)
[![Groq](https://img.shields.io/badge/Groq-LLM-orange.svg)](https://groq.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-UI-FF4B4B.svg)](https://streamlit.io)

## Overview

Biological Agent is a production-ready **MCP Agent** that analyzes consumer products for chemical safety risks. It orchestrates 4 independent MCP servers to filter ingredients, resolve chemical names to Knowledge Graph IDs, evaluate hazards and target organs, and combine risks across multiple products.

**Key Features:**
- True MCP Agent with 4 independent servers communicating via stdio JSON-RPC
- Multi-product analysis with organ overlap detection
- LLM fallback for chemicals not in Knowledge Graph
- Token budget management (respects Groq free tier limits)
- Safety-first design (no partial matches, conservative fallback)
- Streamlit UI with live logging and JSON export

## What It Does

**Input:**
```json
{
  "products_list": [
    {
      "product_id": "1",
      "product_name": "Moisturizing Cream",
      "product_usage": "cosmetics",
      "ingredient_list": [
        {"name": "AQUA"},
        {"name": "SODIUM LAURETH SULFATE"}
      ]
    }
  ]
}
```
## Installation

# Clone
git clone https://github.com/yourusername/biological-agent.git
cd biological-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=yourpassword
GROQ_API_KEY=gsk_your_key_here
EOF

# Verify installation
python -c "import config; config.validate()"
Running the Application
Streamlit UI (Recommended):

bash
streamlit run app.py
# Open http://localhost:8501
