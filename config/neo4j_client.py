"""Neo4j database connection"""

import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

class Neo4jClient:
    def __init__(self):
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER")
        self.password = os.getenv("NEO4J_PASSWORD")
        self.driver = None
    
    def connect(self):
        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(self.user, self.password)
        )
        return self.driver
    
    def close(self):
        if self.driver:
            self.driver.close()
    
    def test(self):
        try:
            self.connect()
            with self.driver.session() as session:
                result = session.run("RETURN 1 as test")
                print(f"✅ Neo4j connected: {result.single()['test']}")
            return True
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False
        finally:
            self.close()

if __name__ == "__main__":
    client = Neo4jClient()
    client.test()