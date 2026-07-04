"""
server.py — FastAPI бэкенд системы NornGraph.
Запуск: python server.py
"""

import os
import re
import json
import asyncio
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
    # Извлекаем корни слов (первые 5-6 букв для слов длиннее 3 символов) для русской морфологии
    keywords = [w[:5].lower() for w in re.split(r'\W+', question) if len(w) > 3]

    if not keywords:
        return ""

    # Строим скоринг: узел получает баллы за каждое найденное слово
    score_cases = " + ".join([f"(CASE WHEN toLower(n.name) CONTAINS '{kw}' THEN 1 ELSE 0 END)" for kw in keywords[:6]])

    cypher = f"""
    MATCH (n)
    WITH n, ({score_cases}) AS score
    WHERE score > 0
    ORDER BY score DESC
    LIMIT 15
    CALL {{
        WITH n
        MATCH path = (n)-[*1..{max_hops}]-(related)
        RETURN related, relationships(path) as rels
        LIMIT 30
    }}
    RETURN n.name as center, n.label as center_type,
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
            context_parts.append(
                f"{record['center']} ({record['center_type']}) "
                f"--[{' -> '.join(record['rel_types'])}]--> "
                f"{record['related_name']} ({record['related_type']})"
            )

    return "\n".join(context_parts) if context_parts else ""


def extract_entities_with_llm(text: str) -> dict:
    """Извлекает сущности и связи из текста через RouterAI."""
    prompt = f"""Ты — специализированный экспертный агент по извлечению онтологических знаний для металлургической отрасли (никель, медь, металлы платиновой группы).
Проанализируй следующий текст и извлеки из него сущности и связи в виде JSON. Обрати внимание на маркеры страниц (например, --- [СТРАНИЦА 5] ---).

Текст:
{text[:5000]}

СТРОГИЕ ПРАВИЛА ИЗВЛЕЧЕНИЯ:
1. Числовые значения параметров (температуры, давления, концентрации, время, проценты извлечения металлов) — обязательно выноси как отдельные узлы Property с атрибутом value.
   Пример: "temperature_120c" (name: "Температура 120°C", value: "120"), "recovery_98" (name: "Извлечение палладия 98%", value: "98").
2. Связывай свойства с соответствующими экспериментами, материалами или процессами с помощью связей (condition_of, describes).
3. Противоречия в данных: если в тексте утверждается, что какой-то метод или параметр не работает, опровергает предыдущие данные или снижает показатели по сравнению с другими исследованиями (слова "однако", "в отличие от", "опровергает", "падает до"), обязательно создай связь CONTRADICTS между конфликтующими узлами. В свойствах связи укажи reason.
4. Эксперты, авторы и публикации: связывай авторов с их публикациями (authored_by) и организациями (affiliated_with).
5. ЦИТИРОВАНИЕ: Для КАЖДОГО узла постарайся найти точный номер страницы (где он был упомянут) и короткую цитату (до 100 символов), подтверждающую этот факт.

Верни JSON строго следующей структуры:
{{
  "nodes": [
    {{"id": "уникальный_id", "label": "тип_узла", "name": "название", "value": "опциональное_значение", "page": 1, "quote": "точная цитата из текста"}}
  ],
  "edges": [
    {{"source": "id_источника", "target": "id_цели", "type": "тип_связи", "reason": "почему_противоречит_или_описание"}}
  ]
}}

Допустимые типы узлов: Material, Process, Equipment, Property, Experiment, Expert, Publication, Organization, Condition
Допустимые типы связей: uses, applies, produces_output, operated_by, condition_of, authored_by, affiliated_with, describes, contradicts

Только JSON, без пояснений."""

    response = llm_client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=2000
    )

    raw = response.choices[0].message.content
    json_match = re.search(r'\{[\s\S]*\}', raw)
    if not json_match:
        return {"nodes": [], "edges": []}

    try:
        return json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return {"nodes": [], "edges": []}


def merge_entities_to_neo4j(graph_data: dict, filename: str = None) -> dict:
    """Сохраняет извлечённые сущности в Neo4j."""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    created_nodes = []
    created_edges = []

    with neo4j_driver.session() as session:
        # Создаем узел документа, если передано имя файла
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

            session.run(
                f"MERGE (n:{label} {{id: $id}}) SET n.name = $name, n.label = $label",
                {"id": node_id, "name": name, "label": label}
            )
            created_nodes.append({"id": node_id, "label": label, "name": name})

            # Связываем узел с документом
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
    """Выгружает граф в формате для vis-network.js."""
    nodes_map = {}
    edges_list = []

    with neo4j_driver.session() as session:
        # Получаем все узлы, кроме Document (они только для RAG, не для визуализации)
        all_nodes = session.run(
            "MATCH (n) WHERE NOT n:Document "
            "RETURN properties(n).id as id, n.label as label, n.name as name, n.value as value LIMIT $limit",
            {"limit": limit}
        )
        for record in all_nodes:
            node_id = record["id"]
            if node_id:
                nodes_map[node_id] = {
                    "id": node_id,
                    "label": record["name"] or node_id,
                    "group": record["label"] or "Unknown",
                    "title": f"{record['label']}: {record['name']}" + (f"\nValue: {record['value']}" if record["value"] else "")
                }

        # Получаем рёбра, исключая MENTIONED_IN (они скрытые, только для RAG)
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
        # 2. Извлекаем онтологию
        graph_data = extract_entities_with_llm(text)
        
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

