"""
populate_graph.py — ETL скрипт для наполнения Neo4j из NotebookLM.
Запускается ОДИН РАЗ разработчиком. Не требует RouterAI.
"""

import os
import json
import subprocess
import time
import re
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NOTEBOOK_ID = os.getenv("NOTEBOOK_ID")

# ─── Промпты для извлечения онтологии ───────────────────────────────────────

PROMPTS = [
    {
        "name": "materials_and_processes",
        "query": (
            "Проанализируй все документы и найди примеры экспериментов с металлургическими материалами. "
            "Для каждого эксперимента верни JSON-объект со следующей структурой: "
            '{"nodes": [{"id": "уникальный_id", "label": "тип_узла", "name": "название"}], '
            '"edges": [{"source": "id1", "target": "id2", "type": "тип_связи"}]}. '
            "Типы узлов: Material (материал/сплав/руда), Process (процесс/метод), "
            "Property (свойство с числовым значением), Experiment (описание эксперимента). "
            "Типы связей: uses (эксперимент использует материал), applies (эксперимент применяет процесс), "
            "produces_output (эксперимент производит свойство). "
            "Верни минимум 15 уникальных экспериментов. Только JSON, без пояснений."
        )
    },
    {
        "name": "equipment_and_conditions",
        "query": (
            "Найди в документах всё оборудование, установки и технологические условия (температура, давление, время). "
            "Верни JSON: "
            '{"nodes": [{"id": "id", "label": "тип", "name": "название", "value": "значение если есть"}], '
            '"edges": [{"source": "id1", "target": "id2", "type": "тип"}]}. '
            "Типы узлов: Equipment (печь, реактор, автоклав, флотомашина), Condition (T=150C, P=2atm). "
            "Типы связей: operated_by (процесс выполняется на оборудовании), "
            "condition_of (условие принадлежит эксперименту). "
            "Верни 10+ уникальных единиц оборудования. Только JSON."
        )
    },
    {
        "name": "experts_and_publications",
        "query": (
            "Найди всех авторов, исследователей, организации и публикации упомянутые в документах. "
            "Верни JSON: "
            '{"nodes": [{"id": "id", "label": "тип", "name": "название"}], '
            '"edges": [{"source": "id1", "target": "id2", "type": "тип"}]}. '
            "Типы узлов: Expert (ФИО исследователя), Organization (институт, компания), "
            "Publication (статья, отчёт, книга). "
            "Типы связей: authored_by (публикация написана экспертом), "
            "affiliated_with (эксперт из организации), describes (публикация описывает эксперимент). "
            "Только JSON."
        )
    },
    {
        "name": "contradictions_and_gaps",
        "query": (
            "Найди в документах случаи, когда разные источники противоречат друг другу: "
            "разные значения одного показателя для одного материала/процесса, "
            "конфликтующие рекомендации по технологическим режимам. "
            "Верни JSON: "
            '{"nodes": [{"id": "id", "label": "тип", "name": "название"}], '
            '"edges": [{"source": "id1", "target": "id2", "type": "contradicts", '
            '"reason": "краткое описание противоречия"}]}. '
            "Верни минимум 3 противоречия. Только JSON."
        )
    }
]

# ─── Утилиты ─────────────────────────────────────────────────────────────────

def query_notebooklm(notebook_id: str, question: str) -> str:
    """Отправляет запрос к NotebookLM через CLI и возвращает текст ответа."""
    print(f"  [NLM] Запрос к NotebookLM...")
    result = subprocess.run(
        ["nlm", "query", "notebook", notebook_id, question],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"NLM ошибка: {result.stderr}")
    
    try:
        data = json.loads(result.stdout)
        return data.get("answer", "")
    except Exception as e:
        raise ValueError(f"Не удалось распарсить вывод NLM CLI как JSON: {e}. Output: {result.stdout[:200]}")



def extract_json_from_response(text: str) -> dict:
    """Вытаскивает JSON из текста ответа NLM, чинит битый JSON."""
    # Ищем JSON-блок в ответе
    json_match = re.search(r'\{[\s\S]*\}', text)
    if not json_match:
        raise ValueError("JSON не найден в ответе")

    raw_json = json_match.group(0)

    # Пробуем json-repair если стандартный парсер падает
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json
            repaired = repair_json(raw_json)
            return json.loads(repaired)
        except Exception as e:
            raise ValueError(f"Не удалось распарсить JSON: {e}")


def merge_graph_data(driver, graph_data: dict):
    """Записывает узлы и рёбра в Neo4j через MERGE (без дублей)."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    with driver.session() as session:
        # Создаём/обновляем узлы
        for node in nodes:
            node_id = node.get("id", "").strip()
            label = node.get("label", "Unknown").strip()
            name = node.get("name", node_id).strip()
            value = node.get("value", None)
            
            if not node_id or not name:
                continue

            cypher = (
                f"MERGE (n:{label} {{id: $id}}) "
                "SET n.name = $name, n.label = $label"
            )
            params = {"id": node_id, "name": name, "label": label}
            if value:
                cypher += ", n.value = $value"
                params["value"] = value

            session.run(cypher, params)

        # Создаём/обновляем рёбра
        for edge in edges:
            src = edge.get("source", "").strip()
            tgt = edge.get("target", "").strip()
            rel_type = edge.get("type", "RELATED").strip().upper().replace(" ", "_")
            reason = edge.get("reason", None)

            if not src or not tgt:
                continue

            cypher = (
                "MATCH (a {id: $src}), (b {id: $tgt}) "
                f"MERGE (a)-[r:{rel_type}]->(b)"
            )
            params = {"src": src, "tgt": tgt}
            if reason:
                cypher += " SET r.reason = $reason"
                params["reason"] = reason

            session.run(cypher, params)

    print(f"  [Neo4j] Загружено: {len(nodes)} узлов, {len(edges)} рёбер")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("NornGraph ETL — Наполнение графа знаний")
    print("=" * 60)

    # Подключаемся к Neo4j
    print("\n[1/3] Подключение к Neo4j Aura...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    driver.verify_connectivity()
    print("  ✅ Neo4j подключён")

    # Очищаем базу
    print("\n[2/3] Очистка базы данных...")
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    print("  ✅ База очищена")

    # Создаём индексы для ускорения MERGE
    with driver.session() as session:
        for label in ["Material", "Process", "Equipment", "Property",
                      "Experiment", "Expert", "Publication", "Organization", "Condition"]:
            try:
                session.run(f"CREATE INDEX {label.lower()}_id IF NOT EXISTS FOR (n:{label}) ON (n.id)")
            except Exception:
                pass
    print("  ✅ Индексы созданы")

    # Запускаем запросы к NotebookLM и заливаем в Neo4j
    print(f"\n[3/3] Извлечение онтологии из NotebookLM ({len(PROMPTS)} запросов)...")

    total_nodes = 0
    total_edges = 0

    for i, prompt_cfg in enumerate(PROMPTS):
        name = prompt_cfg["name"]
        query = prompt_cfg["query"]

        print(f"\n  [{i+1}/{len(PROMPTS)}] Пакет: {name}")

        try:
            raw_response = query_notebooklm(NOTEBOOK_ID, query)
            graph_data = extract_json_from_response(raw_response)
            merge_graph_data(driver, graph_data)
            total_nodes += len(graph_data.get("nodes", []))
            total_edges += len(graph_data.get("edges", []))
            print(f"  ✅ Пакет {name} успешно обработан")
        except Exception as e:
            print(f"  ⚠️  Ошибка в пакете {name}: {e}")
            print("  Продолжаем со следующим пакетом...")

        # Пауза между запросами
        if i < len(PROMPTS) - 1:
            print("  Ожидание 5 сек...")
            time.sleep(5)

    # Итог
    driver.close()
    print("\n" + "=" * 60)
    print(f"✅ ETL завершён!")
    print(f"   Всего загружено: ~{total_nodes} узлов, ~{total_edges} рёбер")
    print("   Запустите server.py для старта веб-интерфейса")
    print("=" * 60)


if __name__ == "__main__":
    main()
