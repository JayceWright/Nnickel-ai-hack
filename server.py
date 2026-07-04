"""
server.py — FastAPI бэкенд системы NornGraph.
Запуск: python server.py
"""

import os
import re
import json
import asyncio
import urllib.parse
import requests
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Form, Security
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from openai import OpenAI
from neo4j import GraphDatabase
from dotenv import load_dotenv

load_dotenv()

# ─── Конфиг ──────────────────────────────────────────────────────────────────

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
ROUTERAI_KEY = os.getenv("ROUTERAI_KEY")
ROUTERAI_BASE_URL = os.getenv("ROUTERAI_BASE_URL", "https://api.routerai.ru/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek/deepseek-chat")
API_KEY = os.getenv("API_KEY", "")
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# ─── Инициализация ────────────────────────────────────────────────────────────

app = FastAPI(title="NornGraph API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

llm_client = OpenAI(
    base_url=ROUTERAI_BASE_URL,
    api_key=ROUTERAI_KEY
)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    """Проверяет API-ключ. Если API_KEY не задан в .env — защита отключена (демо-режим)."""
    if API_KEY and api_key != API_KEY:
        raise HTTPException(
            status_code=403,
            detail="⛔ Доступ запрещён. Укажите корректный X-API-Key заголовок."
        )
    return api_key

# ─── Модели данных ────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    max_hops: int = 2


class UrlUploadRequest(BaseModel):
    url: str
    use_vision: bool = False


class UploadStatus:
    """In-memory статус загрузки файлов."""
    _jobs: dict = {}

    @classmethod
    def set(cls, job_id: str, data: dict):
        cls._jobs[job_id] = data

    @classmethod
    def get(cls, job_id: str) -> dict:
        return cls._jobs.get(job_id, {"status": "not_found"})


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def get_graph_context(question: str, max_hops: int = 2) -> str:
    """Извлекает релевантный контекст из Neo4j для GraphRAG."""
    # Корни слов для русской морфологии + числовые диапазоны
    keywords = [w[:5].lower() for w in re.split(r'\W+', question) if len(w) > 3]
    
    # Извлекаем числовые условия из вопроса (e.g. "< 200", "> 50", "300 мг/л")
    num_conditions = []
    for m in re.finditer(r'([<>]=?|около|не более|не менее|до|от)\s*(\d+(?:[.,]\d+)?)', question):
        op_raw = m.group(1).strip()
        val = float(m.group(2).replace(',', '.'))
        op_map = {'<': '<', '<=': '<=', '>': '>', '>=': '>=', 'до': '<=', 'от': '>=', 'не более': '<=', 'не менее': '>='}
        cypher_op = op_map.get(op_raw, None)
        if cypher_op:
            num_conditions.append(f"(n.value_num IS NOT NULL AND n.value_num {cypher_op} {val})")

    if not keywords:
        return ""

    # Строим скоринг: узел получает баллы за каждое найденное слово
    score_cases = " + ".join([f"(CASE WHEN toLower(n.name) CONTAINS '{kw}' THEN 1 ELSE 0 END)" for kw in keywords[:6]])
    
    # Дополнительный фильтр по числовым условиям
    numeric_filter = " OR " + " OR ".join(num_conditions) if num_conditions else ""

    cypher = f"""
    MATCH (n)
    WITH n, ({score_cases}) AS score
    WHERE score > 0{numeric_filter}
    ORDER BY score DESC
    LIMIT 15
    CALL {{
        WITH n
        MATCH path = (n)-[*1..{max_hops}]-(related)
        RETURN related, relationships(path) as rels
        LIMIT 30
    }}
    RETURN n.name as center, n.label as center_type, n.geography as geo, n.year as yr, n.confidence as conf,
           related.name as related_name, related.label as related_type,
           [r in rels | type(r) + 
            CASE WHEN type(r)='MENTIONED_IN'
                 THEN ' (документ ' + related.name + 
                      CASE WHEN r.page IS NOT NULL THEN ', стр.' + toString(r.page) ELSE '' END + 
                      CASE WHEN r.quote IS NOT NULL AND r.quote <> '' THEN ' цитата: \"' + r.quote + '\"' ELSE '' END + 
                      ')' 
                 ELSE '' END] as rel_types
    LIMIT 150
    """

    context_parts = []
    with neo4j_driver.session() as session:
        results = session.run(cypher)
        for record in results:
            meta = []
            if record.get('geo'): meta.append(f"🌍{record['geo']}")
            if record.get('yr'): meta.append(f"📅{record['yr']}")
            if record.get('conf'): meta.append(f"✓{record['conf']}")
            meta_str = f" [{', '.join(meta)}]" if meta else ""
            context_parts.append(
                f"{record['center']} ({record['center_type']}){meta_str} "
                f"--[{' -> '.join(record['rel_types'])}]--> "
                f"{record['related_name']} ({record['related_type']})"
            )

    return "\n".join(context_parts) if context_parts else ""


def extract_entities_with_llm(text: str) -> dict:
    """Извлекает сущности и связи из текста через RouterAI."""
    prompt = f"""Ты — специализированный экспертный агент по извлечению онтологических знаний для металлургической отрасли (никель, медь, металлы платиновой группы).
Проанализируй следующий текст и извлеки из него сущности и связи в виде JSON. Обрати внимание на маркеры страниц (например, --- [СТРАНИЦА 5] ---).

Синоним-словарь для нормализации терминов (обязательно используй каноническое название, но не создавай дубли):
- ПВП = Печь взвешенной плавки = Fluidized Bed Furnace = Взвешенная плавка
- ЭЭ = Электроэкстракция = Electrowinning = Электроосаждение
- АОВ = Автоклавное окислительное выщелачивание = POX = Pressure Oxidation
- МПГ = Металлы платиновой группы = ПГМ = PGM = Platinum Group Metals
- ЛДП = Линия двойного питания = Электролитная ванна

Текст:
{text}

СТРОГИЕ ПРАВИЛА ИЗВЛЕЧЕНИЯ:
1. Числовые значения параметров (температуры, давления, концентрации, время, проценты извлечения металлов) — обязательно выноси как отдельные узлы Property с атрибутом value (числовое значение без единиц, только число) и value_unit (единица: мг/л, °C, г/л, %, т/сут, м³/ч).
   Пример: {{"id": "conc_sulfate_250", "label": "Property", "name": "Концентрация сульфатов 250 мг/л", "value": 250, "value_unit": "мг/л"}}.
2. Связывай свойства с соответствующими экспериментами, материалами или процессами с помощью связей (condition_of, describes).
3. Противоречия в данных: если в тексте утверждается, что какой-то метод или параметр не работает, опровергает предыдущие данные или снижает показатели (слова "однако", "в отличие от", "опровергает", "падает до"), обязательно создай связь CONTRADICTS между конфликтующими узлами. В свойствах связи укажи reason.
4. Эксперты, авторы и публикации: связывай авторов с их публикациями (authored_by) и организациями (affiliated_with). Для Publication указывай year (год публикации, число) и geography ("Россия" или "Зарубежье" или название страны).
5. ЦИТИРОВАНИЕ: Для КАЖДОГО узла постарайся найти точный номер страницы (page) и короткую цитату (quote, до 100 символов), подтверждающую этот факт.
6. ДОСТОВЕРНОСТЬ: Для каждого Experiment и Publication выставляй поле confidence: "high" (рецензируемая статья, диссертация, патент), "medium" (технический отчет, презентация), "low" (устное сообщение, не верифицировано).
7. ГЕОГРАФИЯ: Для Process и Experiment, если в тексте упомянута страна или регион, добавляй поле geography: "Россия", "США", "Финляндия" и т.д. Если не упомянута — не добавляй.
8. ЕСЛИ В ТЕКСТЕ НЕТ ЭКСПЕРИМЕНТАЛЬНЫХ ДАННЫХ (например, это оглавление или доклад), ВСЕ РАВНО извлекай концепты (Process, Material) и людей (Expert). Граф никогда не должен быть пустым при наличии осмысленного текста.

Верни JSON строго следующей структуры:
{{
  "nodes": [
    {{"id": "уникальный_id", "label": "тип_узла", "name": "название", "value": 120, "value_unit": "°C", "page": 1, "quote": "цитата", "confidence": "high", "year": 2021, "geography": "Россия"}}
  ],
  "edges": [
    {{"source": "id_источника", "target": "id_цели", "type": "тип_связи", "reason": "описание"}}
  ]
}}

Допустимые типы узлов: Material, Process, Equipment, Property, Experiment, Expert, Publication, Organization, Condition
Допустимые типы связей: uses, applies, produces_output, operated_by, condition_of, authored_by, affiliated_with, describes, contradicts

Только JSON, без пояснений."""

    response = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=3000
    )

    raw = response.choices[0].message.content
    with open("llm_debug_output.txt", "w", encoding="utf-8") as f:
        f.write(raw)
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        return {"nodes": [], "edges": []}

    try:
        return json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return {"nodes": [], "edges": []}


def extract_entities_from_large_text(text: str) -> dict:
    """Разбивает большой текст на перекрывающиеся чанки и извлекает сущности из каждого чанка, объединяя результаты."""
    # Если текст короткий, обрабатываем целиком
    if len(text) <= 12000:
        return extract_entities_with_llm(text)
        
    chunk_size = 10000
    overlap = 1500
    
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        # Стараемся резать по концам абзацев, если возможно
        if end < len(text):
            next_newline = text.find("\n", end - 500, end + 500)
            if next_newline != -1:
                end = next_newline
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap

    print(f"🧩 Разделение текста на {len(chunks)} частей для анализа LLM...")
    
    all_nodes = {}
    all_edges = []
    seen_edges = set()

    for idx, chunk in enumerate(chunks):
        print(f"🧠 [Chunk {idx+1}/{len(chunks)}] Извлечение онтологии из {len(chunk)} символов...")
        chunk_data = extract_entities_with_llm(chunk)
        
        # Слияние узлов
        for node in chunk_data.get("nodes", []):
            node_id = str(node.get("id", "")).strip()
            if not node_id:
                continue
            if node_id not in all_nodes:
                all_nodes[node_id] = node
            else:
                # Дополняем свойства, если в других чанках их не было
                existing = all_nodes[node_id]
                for key in ["value", "value_unit", "confidence", "year", "geography", "page", "quote"]:
                    if node.get(key) is not None and existing.get(key) is None:
                        existing[key] = node[key]

        # Слияние ребер
        for edge in chunk_data.get("edges", []):
            src = str(edge.get("source", "")).strip()
            tgt = str(edge.get("target", "")).strip()
            rel_type = edge.get("type", "RELATED").strip().upper().replace(" ", "_")
            if not src or not tgt:
                continue
            edge_key = (src, tgt, rel_type)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                all_edges.append(edge)

    return {
        "nodes": list(all_nodes.values()),
        "edges": all_edges
    }


def merge_entities_to_neo4j(graph_data: dict, filename: str = None) -> dict:
    """Сохраняет извлечённые сущности в Neo4j (с полями year, geography, confidence, value_num)."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    created_nodes = []
    created_edges = []

    with neo4j_driver.session() as session:
        doc_id = None
        if filename:
            doc_id = f"doc_{filename.lower().replace(' ', '_')}"
            session.run(
                "MERGE (d:Document {id: $id}) SET d.name = $name, d.label = 'Document'",
                {"id": doc_id, "name": filename}
            )

        for node in nodes:
            node_id = str(node.get("id", "")).strip()
            label = node.get("label", "Unknown").strip()
            name = node.get("name", node_id).strip()
            if not node_id or not name:
                continue

            # Базовые поля — всегда
            set_parts = ["n.name = $name", "n.label = $label"]
            params: dict = {"id": node_id, "name": name, "label": label}

            # value — строковое представление
            if node.get("value") is not None:
                set_parts.append("n.value = $value")
                params["value"] = str(node["value"])

            # value_num — числовое для Cypher-фильтрации
            raw_val = node.get("value")
            if raw_val is not None:
                try:
                    params["value_num"] = float(str(raw_val).replace(",", "."))
                    set_parts.append("n.value_num = $value_num")
                except (ValueError, TypeError):
                    pass

            # value_unit — единица измерения
            if node.get("value_unit"):
                set_parts.append("n.value_unit = $value_unit")
                params["value_unit"] = str(node["value_unit"])

            # confidence — high / medium / low
            if node.get("confidence"):
                set_parts.append("n.confidence = $confidence")
                params["confidence"] = str(node["confidence"])

            # year — год публикации / исследования
            if node.get("year"):
                try:
                    set_parts.append("n.year = $year")
                    params["year"] = int(node["year"])
                except (ValueError, TypeError):
                    pass

            # geography — страна / регион
            if node.get("geography"):
                set_parts.append("n.geography = $geography")
                params["geography"] = str(node["geography"])

            session.run(
                f"MERGE (n:{label} {{id: $id}}) SET {', '.join(set_parts)}",
                params
            )
            created_nodes.append({"id": node_id, "label": label, "name": name})

            # Связываем с документом через MENTIONED_IN
            if doc_id:
                page_num = node.get("page")
                quote_text = str(node.get("quote", "")).replace('"', "'").strip()
                q = "MATCH (n {id: $node_id}), (d:Document {id: $doc_id}) MERGE (n)-[r:MENTIONED_IN]->(d) "
                if page_num is not None:
                    q += f"SET r.page = {page_num} "
                if quote_text:
                    q += "SET r.quote = $quote "
                session.run(q, {"node_id": node_id, "doc_id": doc_id, "quote": quote_text})

        for edge in edges:
            src = str(edge.get("source", "")).strip()
            tgt = str(edge.get("target", "")).strip()
            rel_type = edge.get("type", "RELATED").strip().upper().replace(" ", "_")
            if not src or not tgt:
                continue
            reason = edge.get("reason", "")
            if reason:
                session.run(
                    f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) MERGE (a)-[r:{rel_type}]->(b) SET r.reason = $reason",
                    {"src": src, "tgt": tgt, "reason": reason}
                )
            else:
                session.run(
                    f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) MERGE (a)-[r:{rel_type}]->(b)",
                    {"src": src, "tgt": tgt}
                )
            created_edges.append({"source": src, "target": tgt, "type": rel_type})

    return {"nodes": created_nodes, "edges": created_edges}


# ─── API Эндпоинты ────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    """Проверка работоспособности сервиса."""
    try:
        neo4j_driver.verify_connectivity()
        return {"status": "ok", "neo4j": "connected", "model": LLM_MODEL}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/stats")
def get_stats():
    """Статистика графа: количество узлов и рёбер по типам."""
    with neo4j_driver.session() as session:
        node_counts = {}
        for label in ["Material", "Process", "Equipment", "Property",
                      "Experiment", "Expert", "Publication", "Organization", "Condition", "Document"]:
            result = session.run(f"MATCH (n:{label}) RETURN count(n) as cnt")
            node_counts[label] = result.single()["cnt"]

        total_edges = session.run("MATCH ()-[r]->() RETURN count(r) as cnt").single()["cnt"]
        contradicts = session.run("MATCH ()-[r:CONTRADICTS]->() RETURN count(r) as cnt").single()["cnt"]

    return {
        "nodes": node_counts,
        "total_nodes": sum(node_counts.values()),
        "total_edges": total_edges,
        "contradictions": contradicts
    }


@app.get("/api/graph")
def get_graph(limit: int = 500):
    """Выгружает граф в формате для vis-network.js с полной информацией об атрибутах."""
    nodes_map = {}
    edges_list = []

    with neo4j_driver.session() as session:
        # Получаем все узлы, кроме Document
        all_nodes = session.run(
            "MATCH (n) WHERE NOT n:Document "
            "RETURN properties(n) as props, labels(n)[0] as label LIMIT $limit",
            {"limit": limit}
        )
        for record in all_nodes:
            props = record["props"]
            node_id = props.get("id")
            if node_id:
                group = record["label"] or "Unknown"
                name = props.get("name") or node_id
                
                # Создаем информативный тултип (title)
                tooltip = f"Тип: {group}\nИмя: {name}"
                if props.get("value"):
                    unit = props.get("value_unit") or ""
                    tooltip += f"\nЗначение: {props['value']} {unit}".strip()
                if props.get("year"):
                    tooltip += f"\nГод: {props['year']}"
                if props.get("geography"):
                    tooltip += f"\nГеография: {props['geography']}"
                if props.get("confidence"):
                    tooltip += f"\nДостоверность: {props['confidence']}"

                nodes_map[node_id] = {
                    "id": node_id,
                    "label": name,
                    "group": group,
                    "title": tooltip,
                    "properties": props
                }

        # Получаем рёбра, исключая MENTIONED_IN
        all_edges = session.run(
            "MATCH (a)-[r]->(b) "
            "WHERE properties(a).id IS NOT NULL AND properties(b).id IS NOT NULL "
            "AND type(r) <> 'MENTIONED_IN' "
            "RETURN properties(a).id as from, properties(b).id as to, type(r) as type, r.reason as reason LIMIT 1000"
        )
        for record in all_edges:
            edge = {
                "from": record["from"],
                "to": record["to"],
                "label": record["type"].lower(),
                "arrows": "to"
            }
            if record["type"] == "CONTRADICTS":
                edge["color"] = {"color": "#ff4444", "highlight": "#ff0000"}
                edge["dashes"] = True
                edge["title"] = record["reason"] or "Противоречие"
            edges_list.append(edge)

    return {
        "nodes": list(nodes_map.values()),
        "edges": edges_list
    }


class UpdateNodeRequest(BaseModel):
    id: str
    name: str
    value: Optional[str] = None
    value_unit: Optional[str] = None
    year: Optional[int] = None
    geography: Optional[str] = None
    confidence: Optional[str] = None

@app.post("/api/node/update")
def update_node(request: UpdateNodeRequest):
    """Обновляет атрибуты узла в Neo4j."""
    with neo4j_driver.session() as session:
        # Получаем ярлык узла для безопасного MERGE/SET
        res = session.run("MATCH (n {id: $id}) RETURN labels(n)[0] as label", {"id": request.id})
        record = res.single()
        if not record:
            raise HTTPException(status_code=404, detail="Узел не найден")
        
        label = record["label"]
        set_parts = ["n.name = $name"]
        params = {"id": request.id, "name": request.name}

        if request.value is not None:
            set_parts.append("n.value = $value")
            params["value"] = request.value
            try:
                params["value_num"] = float(str(request.value).replace(",", "."))
                set_parts.append("n.value_num = $value_num")
            except (ValueError, TypeError):
                # Если не число, удаляем value_num
                set_parts.append("n.value_num = null")
        else:
            set_parts.append("n.value = null")
            set_parts.append("n.value_num = null")

        if request.value_unit is not None:
            set_parts.append("n.value_unit = $value_unit")
            params["value_unit"] = request.value_unit
        else:
            set_parts.append("n.value_unit = null")

        if request.year is not None:
            set_parts.append("n.year = $year")
            params["year"] = request.year
        else:
            set_parts.append("n.year = null")

        if request.geography is not None:
            set_parts.append("n.geography = $geography")
            params["geography"] = request.geography
        else:
            set_parts.append("n.geography = null")

        if request.confidence is not None:
            set_parts.append("n.confidence = $confidence")
            params["confidence"] = request.confidence
        else:
            set_parts.append("n.confidence = null")

        session.run(
            f"MATCH (n:{label} {{id: $id}}) SET {', '.join(set_parts)}",
            params
        )
    return {"status": "ok"}


@app.delete("/api/node/{node_id}")
def delete_node(node_id: str):
    """Удаляет узел и все его связи из Neo4j."""
    with neo4j_driver.session() as session:
        # Проверяем наличие
        res = session.run("MATCH (n {id: $id}) RETURN count(n) as cnt", {"id": node_id})
        if res.single()["cnt"] == 0:
            raise HTTPException(status_code=404, detail="Узел не найден")
        
        # DETACH DELETE удаляет узел и все связанные рёбра
        session.run("MATCH (n {id: $id}) DETACH DELETE n", {"id": node_id})
    return {"status": "ok"}



@app.post("/api/query")
async def query_graph(request: QueryRequest):
    """GraphRAG: поиск в Neo4j + генерация ответа через RouterAI."""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Вопрос не может быть пустым")

    # Получаем контекст из графа
    context = get_graph_context(request.question, request.max_hops)

    # Формируем промпт
    system_prompt = (
        "Ты — экспертная система NornGraph для научного анализа и поиска данных в металлургии никеля, меди и металлов платиновой группы.\n"
        "Твоя задача — отвечать на вопросы пользователя на основе предоставленного контекста из графа знаний Neo4j.\n\n"
        "ПРАВИЛА И АЛГОРИТМ ОТВЕТА:\n"
        "1. Отвечай строго на основе контекста. Ссылки должны быть точными. Если в связях MENTIONED_IN указана страница, формируй ссылку в формате Markdown: "
        "`[📄 Имя_Файла.pdf, стр. X](/docs/Имя_Файла.pdf#page=X)`. Если файл не PDF, ссылку оставляй `[📄 Имя_Файла.txt](/docs/Имя_Файла.txt)`. Вставляй такие ссылки сразу после утверждений.\n"
        "2. Обязательно приводи короткие точные цитаты курсивом из свойства 'цитата', если они есть в контексте. Форматируй так: *«Цитата из текста»*.\n"
        "3. Выявление противоречий: Если в контексте присутствуют данные из разных исследований или источников, которые противоречат друг другу (например, разные показатели извлечения при одинаковых условиях, или разные температурные режимы), ты ОБЯЗАТЕЛЬНО должен сравнить их в ответе и указать на конфликт:\n"
        "   - 'Согласно источнику А, показатель равен X...'\n"
        "   - 'Однако, согласно источнику Б, при этих же условиях показатель падает до Y...'\n"
        "4. Если контекст пуст, но вопрос касается металлургии, ответь на основе своих общих знаний, но обязательно начни ответ с фразы: 'В текущем графе знаний точных данных не обнаружено, но на основе общих металлургических данных...'.\n"
        "5. Отвечай на русском языке в академическом, научном стиле. Структурируй ответ: краткая выжимка -> подробный анализ -> ссылки на источники с цитатами."
    )

    user_prompt = f"""Контекст из базы знаний Neo4j (граф связей):
{context if context else "--- ДАННЫЕ В ГРАФЕ НЕ НАЙДЕНЫ ---"}

Вопрос: {request.question}

Ответь согласно правилам."""

    response = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.3,
        max_tokens=1500
    )

    answer = response.choices[0].message.content

    return {
        "answer": answer,
        "context_used": bool(context),
        "context_preview": context[:500] if context else None
    }


class ExportRequest(BaseModel):
    question: str
    answer: str

@app.post("/api/export")
def export_answer(request: ExportRequest):
    """Экспортирует Q&A ответ в Markdown-формате."""
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    md = f"""# NornGraph — Экспорт ответа

**Дата:** {ts}  
**Вопрос:** {request.question}

---

{request.answer}

---
*Сгенерировано системой NornGraph · GraphRAG на базе Neo4j + DeepSeek*
"""
    return {"markdown": md, "filename": f"norngraph_export_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.md"}



@app.post("/api/upload")
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...), use_vision: bool = Form(False)):
    """Загружает новый документ и асинхронно расширяет граф."""
    import uuid
    job_id = str(uuid.uuid4())[:8]

    # Читаем файл
    content = await file.read()

    # Сохраняем физически, чтобы можно было отдать по ссылке /docs/...
    docs_dir = Path(__file__).parent / "data" / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    file_path = docs_dir / file.filename
    with open(file_path, "wb") as f:
        f.write(content)

    UploadStatus.set(job_id, {"status": "processing", "filename": file.filename})

    # Запускаем обработку в фоне
    background_tasks.add_task(process_document_background, job_id, str(file_path), file.filename, use_vision)

    return {"job_id": job_id, "status": "processing", "filename": file.filename}


def resolve_yandex_disk_url(url: str):
    """Преобразует ссылку Яндекс.Диска или прямую ссылку в скачиваемый URL и имя файла."""
    if "yadi.sk" in url or "disk.yandex.ru" in url:
        api_url = f"https://cloud-api.yandex.net/v1/disk/public/resources?public_key={urllib.parse.quote(url)}"
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            r = requests.get(api_url, timeout=10, headers=headers)
            if r.status_code == 200:
                data = r.json()
                download_url = data.get("file")
                filename = data.get("name", "document.pdf")
                if download_url:
                    return download_url, filename
        except Exception as e:
            print(f"⚠️ Ошибка при обращении к Yandex.Disk API: {e}")
            raise HTTPException(status_code=400, detail=f"Ошибка Yandex.Disk API: {e}")

        raise HTTPException(status_code=400, detail="Не удалось получить ссылку для скачивания с Яндекс.Диска. Убедитесь, что ссылка публичная.")
    
    # Для прямых ссылок
    parsed = urllib.parse.urlparse(url)
    filename = urllib.parse.unquote(os.path.basename(parsed.path)) or "downloaded_file"
    if not os.path.splitext(filename)[1]:
        filename += ".pdf"
    return url, filename


@app.post("/api/upload_url")
async def upload_by_url(request: UrlUploadRequest, background_tasks: BackgroundTasks):
    """Загружает документ по ссылке (прямой или Яндекс.Диск) и асинхронно расширяет граф."""
    import uuid
    job_id = str(uuid.uuid4())[:8]

    target_url, filename = resolve_yandex_disk_url(request.url)

    # Скачиваем файл
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(target_url, timeout=30, stream=True, headers=headers)
        if r.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Ошибка скачивания файла: HTTP {r.status_code}")
        
        # Сохраняем физически
        docs_dir = Path(__file__).parent / "data" / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        file_path = docs_dir / filename
        
        with open(file_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка скачивания файла: {str(e)}")

    UploadStatus.set(job_id, {"status": "processing", "filename": filename})

    # Запускаем обработку в фоне
    background_tasks.add_task(process_document_background, job_id, str(file_path), filename, request.use_vision)

    return {"job_id": job_id, "status": "processing", "filename": filename}


def parse_pdf_with_vlm(file_path: str) -> str:
    """Конвертирует PDF в Markdown с помощью VLM (Qwen2.5-VL-72B) и PyMuPDF."""
    import fitz
    import base64
    
    doc = fitz.open(file_path)
    text_parts = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # Увеличиваем разрешение
        image_bytes = pix.tobytes("jpeg")
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        
        prompt = "Перепиши содержимое этой страницы документа в чистый формат Markdown. Сохрани все таблицы, заголовки и списки. Верни ТОЛЬКО текст в формате Markdown без дополнительных комментариев."
        
        try:
            response = llm_client.chat.completions.create(
                model="qwen/qwen2.5-vl-72b-instruct",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=2000,
                temperature=0.1
            )
            page_md = response.choices[0].message.content
            text_parts.append(f"--- [СТРАНИЦА {page_num+1}] ---\n{page_md}")
        except Exception as e:
            print(f"⚠️ VLM Error on {file_path} page {page_num+1}: {e}. Fallbacking to PyMuPDF text extraction.")
            page_text = page.get_text()
            text_parts.append(f"--- [СТРАНИЦА {page_num+1}] ---\n{page_text}")
            
    return "\n".join(text_parts)


def parse_pdf_fast(file_path: str) -> str:
    """Моментально конвертирует PDF в Markdown локально через pymupdf4llm (или fitz при ошибке)."""
    try:
        import pymupdf4llm
        # Получаем список словарей, каждый словарь - одна страница
        md_pages = pymupdf4llm.to_markdown(file_path, page_chunks=True)
        text_parts = []
        for i, page_data in enumerate(md_pages):
            page_md = page_data.get("text", "")
            text_parts.append(f"--- [СТРАНИЦА {i+1}] ---\n{page_md}")
        return "\n".join(text_parts)
    except Exception as e:
        print(f"⚠️ Ошибка pymupdf4llm на {file_path}: {e}. Фолбэк на стандартный fitz.")
        import fitz
        doc = fitz.open(file_path)
        text_parts = []
        for i, page in enumerate(doc):
            text_parts.append(f"--- [СТРАНИЦА {i+1}] ---\n{page.get_text()}")
        return "\n".join(text_parts)


async def process_document_background(job_id: str, file_path: str, filename: str, use_vision: bool = False):
    """Фоновая задача: чтение, извлечение сущностей и сохранение в Neo4j."""
    print(f"🔄 [Job: {job_id}] Начало обработки файла: {filename} (Vision: {use_vision})")
    try:
        # 1. Читаем и парсим файл
        if filename.endswith(".pdf"):
            if use_vision:
                print(f"👁️ [Job: {job_id}] Парсинг PDF через VLM (Qwen2.5-VL-72B)...")
                text = parse_pdf_with_vlm(file_path)
            else:
                print(f"⚡ [Job: {job_id}] Молниеносный парсинг PDF через pymupdf4llm...")
                text = parse_pdf_fast(file_path)
        elif filename.endswith(".docx"):
            print(f"📄 [Job: {job_id}] Парсинг DOCX...")
            import docx
            doc = docx.Document(file_path)
            text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
        else:
            print(f"📝 [Job: {job_id}] Чтение текстового файла...")
            with open(file_path, "rb") as f:
                content = f.read()
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = content.decode("cp1251", errors="ignore")
                
        if len(text.strip()) < 10:
            raise ValueError("Файл пуст или текст не распознан")

        print(f"🧠 [Job: {job_id}] Отправка в LLM для извлечения графа (размер текста: {len(text)} симв.)...")
        with open("pdf_text_debug.txt", "w", encoding="utf-8") as f:
            f.write(text)
        # 2. Извлекаем онтологию
        graph_data = extract_entities_from_large_text(text)
        
        print(f"💾 [Job: {job_id}] Сохранение графа в Neo4j...")
        # 3. Сохраняем в Neo4j
        result = merge_entities_to_neo4j(graph_data, filename=filename)
        UploadStatus.set(job_id, {
            "status": "done",
            "filename": filename,
            "nodes_created": len(result["nodes"]),
            "edges_created": len(result["edges"]),
            "new_nodes": result["nodes"],
            "new_edges": result["edges"]
        })
        print(f"✅ [Job: {job_id}] Успешно обработан {filename}. Узлов: {len(result['nodes'])}, Связей: {len(result['edges'])}")
    except Exception as e:
        print(f"❌ [Job: {job_id}] Ошибка при обработке {filename}: {e}")
        UploadStatus.set(job_id, {"status": "error", "filename": filename, "error": str(e)})


@app.get("/api/upload/status/{job_id}")
def get_upload_status(job_id: str):
    """Проверяет статус обработки загруженного документа."""
    return UploadStatus.get(job_id)


@app.get("/api/analytics")
def get_analytics():
    """Аналитика: противоречия и пробелы в данных."""
    with neo4j_driver.session() as session:
        # Противоречия
        contradictions = []
        result = session.run(
            "MATCH (a)-[r:CONTRADICTS]->(b) "
            "RETURN a.name as from_name, b.name as to_name, r.reason as reason LIMIT 20"
        )
        for record in result:
            contradictions.append({
                "from": record["from_name"],
                "to": record["to_name"],
                "reason": record["reason"] or "Нет описания"
            })

        # Пробелы (материалы без связанных процессов)
        gaps = []
        result = session.run(
            "MATCH (m:Material) WHERE NOT (m)-[:USES|:APPLIES|:PRODUCES_OUTPUT]-() "
            "RETURN m.name as name LIMIT 10"
        )
        for record in result:
            gaps.append({"type": "isolated_material", "name": record["name"]})

    return {
        "contradictions": contradictions,
        "gaps": gaps,
        "total_contradictions": len(contradictions),
        "total_gaps": len(gaps)
    }


# ─── Статика ──────────────────────────────────────────────────────────────────

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)

docs_dir = Path(__file__).parent / "data" / "docs"
docs_dir.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.mount("/docs", StaticFiles(directory=str(docs_dir)), name="docs")


@app.get("/")
def serve_ui():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "NornGraph API работает. UI файлы не найдены."}


# ─── Запуск ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print("🚀 Запуск NornGraph API сервера...")
    print(f"   Neo4j: {NEO4J_URI}")
    print(f"   LLM:   {LLM_MODEL} via RouterAI")
    print(f"   UI:    http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001, reload=False)

