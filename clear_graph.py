import os
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

if not NEO4J_URI:
    print("❌ Ошибка: не найдены настройки базы в .env")
    exit(1)

print("⏳ Подключение к базе Neo4j...")
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

with driver.session() as session:
    print("🗑️ Удаление всех узлов и связей...")
    session.run("MATCH (n) DETACH DELETE n")

print("✅ База данных полностью очищена! Теперь можно загружать файлы начисто.")
