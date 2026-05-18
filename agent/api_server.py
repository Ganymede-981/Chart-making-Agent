import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from orchestrate import build_and_run_pipeline
from tools.schema import get_frammer_schema
from tools.sql_query import execute_sql_query

app = FastAPI(title="Frammer Analytics API", version="1.0.0")

class QueryRequest(BaseModel):
    question: str


class DataRequest(BaseModel):
    sql: str


class QueryResponse(BaseModel):
    question:str
    sql:str
    xml:str
    insights:str
    error:str
    chart_data: dict

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_path = Path(__file__).parent / "templates" / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/api/query", response_model=QueryResponse)
async def run_query(req: QueryRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    result = await build_and_run_pipeline(req.question)

    specs = result.get("chart_specs", [])

    chart_data: dict = {}
    first_sql = ""
    for i, spec in enumerate(specs):
        chart_title  = spec.get("chart_attrs", {}).get("title", "")
        sub_question = spec.get("sub_question", f"Chart {i+1}")
        records      = spec.get("records", [])
        if chart_title:
            chart_data[chart_title] = records
        chart_data[sub_question] = records
        if i == 0:
            first_sql = spec.get("sql_query", "")

    return QueryResponse(
        question=req.question,
        sql=first_sql,
        xml=result.get("chart_json", ""),
        insights=result.get("insights", ""),
        error=result.get("error", ""),
        chart_data=jsonable_encoder(chart_data),
    )


@app.get("/api/tables")
async def get_schema():
    schema_str = get_frammer_schema()
    tables = {}
    current_table = None
    for line in schema_str.splitlines():
        if line.startswith("Table:"):
            current_table = line.replace("Table:", "").strip()
            tables[current_table] = []
        elif line.startswith("Columns:") and current_table:
            col_part = line.replace("Columns:", "").strip()
            tables[current_table] = [
                c.split("(")[0].strip() for c in col_part.split(",")
            ]
    return {"tables": tables}


@app.post("/api/data")
async def get_data(req: DataRequest):
    if not req.sql.strip():
        raise HTTPException(status_code=400, detail="SQL cannot be empty.")

    raw = execute_sql_query(req.sql)
    parsed = json.loads(raw)

    if "error" in parsed:
        raise HTTPException(status_code=400, detail=parsed["error"])

    return {"records": parsed.get("data", [])}

