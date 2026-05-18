import asyncio
import json
import os
from typing import Any, Dict, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph

from tools import (
    execute_sql_query,
    generate_plotly_chart,
    get_frammer_schema,
    retrieve_metric_definitions,
)

load_dotenv()
llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)

class AgentState(TypedDict):
    question:str
    semantic_context:str
    schema:str
    sql_query:str
    chart_attributes:Dict[str, Any]
    data:str 
    chart_json:str
    insights:str
    error:str
    retry_count:int

async def build_and_run_pipeline(question: str) -> AgentState:
    def retrieve_context(state: AgentState):
        result = retrieve_metric_definitions(state["question"])
        return {"semantic_context": result}

    def get_schema(state: AgentState):
        result = get_frammer_schema()
        return {"schema": result}

    def generate_sql(state: AgentState):
        prompt = PromptTemplate.from_template(
            "Database Schema:\n{schema}\n\n"
            "Business Metric Definitions: {context}\n\n"
            "Previous Error (fix this if present): {error}\n\n"
            "Question: {question}\n\n"
            "Write a valid PostgreSQL SELECT query to answer the question.\n"
            "CRITICAL RULES:\n"
            "1. ONLY use EXACT table and column names from the Database Schema above.\n"
            "2. DO NOT guess table names. Do not drop plurals (e.g. use 'channel_metrics', NOT 'channel_metric').\n"
            "3. Return ONLY the raw SQL query string — no markdown fences, no formatting, no explanation."
        )
        raw = llm.invoke(
            prompt.format(
                schema=state.get("schema", ""),
                context=state.get("semantic_context", ""),
                error=state.get("error", ""),
                question=state["question"],
            )
        ).content
        query = raw.strip().strip("```sql").strip("```").strip()
        return {"sql_query": query, "error": ""}

    def decide_chart_attributes(state: AgentState):
        prompt = PromptTemplate.from_template(
            "You are a data visualisation expert.\n\n"
            "SQL query:\n{sql}\n\n"
            "User question: {question}\n\n"
            "Respond with a single valid JSON object (no markdown) with these keys:\n"
            "  type    — one of: bar, line, scatter, pie\n"
            "  x_axis  — exact column name for the X axis\n"
            "  y_axis  — exact column name for the Y axis\n"
            "  title   — a short, descriptive chart title\n\n"
            "Return ONLY the JSON object."
        )
        raw = llm.invoke(
            prompt.format(sql=state.get("sql_query", ""), question=state["question"])
        ).content.strip()
        raw = raw.strip("```json").strip("```").strip()
        try:
            attrs = json.loads(raw)
        except json.JSONDecodeError:
            attrs = {}

        return {"chart_attributes": attrs}

    def execute_sql(state: AgentState):
        result = execute_sql_query(
            state["sql_query"],
            chart_attributes=state.get("chart_attributes", {}),
        )
        parsed = json.loads(result)
        if "error" in parsed:
            return {
                "error": parsed["error"],
                "data": "[]",
                "retry_count": state.get("retry_count", 0) + 1,
            }
        return {"data": result, "error": ""}

    def generate_chart(state: AgentState):
        raw_data = state.get("data", "{}")
        try:
            payload = json.loads(raw_data)
            records = payload.get("data", [])
            attrs   = payload.get("chart_attributes", state.get("chart_attributes", {}))
        except (json.JSONDecodeError, AttributeError):
            records = []
            attrs   = state.get("chart_attributes", {})

        if not records:
            return {"chart_json": "{}"}

        result = generate_plotly_chart(data_records=records, chart_attributes=attrs)
        return {"chart_json": result}

    def generate_insights(state: AgentState):
        raw_data = state.get("data", "{}")
        try:
            payload    = json.loads(raw_data)
            data_rows  = payload.get("data", [])
            data_str   = json.dumps(data_rows, indent=2)
        except (json.JSONDecodeError, AttributeError):
            data_str = raw_data

        if not data_str or data_str in ("[]", "{}"):
            return {"insights": "No data was returned by the query — cannot generate insights."}

        prompt = PromptTemplate.from_template(
            "You are a business analyst. Analyse the following dataset and provide "
            "2-3 concise, actionable bullet-point insights:\n\n{data}"
        )
        insights = llm.invoke(prompt.format(data=data_str)).content
        return {"insights": insights}

    def handle_sql_error(state: AgentState):
        return {
            "insights": (
                f"Could not generate a valid SQL query after "
                f"{state.get('retry_count', 3)} attempts.\n"
                f"Last error: {state.get('error', 'Unknown error')}"
            ),
            "chart_json": "{}",
        }

    def route_after_sql(state: AgentState) -> str:
        if state.get("error"):
            return "generate_sql" if state.get("retry_count", 0) < 3 else "handle_sql_error"
        return "generate_chart"

    workflow = StateGraph(AgentState)

    workflow.add_node("retrieve_context", retrieve_context)
    workflow.add_node("get_schema", get_schema)
    workflow.add_node("generate_sql", generate_sql)
    workflow.add_node("decide_chart_attributes", decide_chart_attributes)
    workflow.add_node("execute_sql", execute_sql)
    workflow.add_node("generate_chart", generate_chart)
    workflow.add_node("generate_insights", generate_insights)
    workflow.add_node("handle_sql_error", handle_sql_error)

    workflow.set_entry_point("retrieve_context")
    workflow.add_edge("retrieve_context", "get_schema")
    workflow.add_edge("get_schema", "generate_sql")
    workflow.add_edge("generate_sql", "decide_chart_attributes")
    workflow.add_edge("decide_chart_attributes", "execute_sql")
    workflow.add_conditional_edges(
        "execute_sql",
        route_after_sql,
        {
            "generate_sql": "generate_sql",
            "handle_sql_error": "handle_sql_error",
            "generate_chart": "generate_chart",
        },
    )
    workflow.add_edge("generate_chart", "generate_insights")
    workflow.add_edge("generate_insights", END)
    workflow.add_edge("handle_sql_error",  END)

    app = workflow.compile()

    return await app.ainvoke(
        {
            "question": question,
            "semantic_context": "",
            "schema": "",
            "sql_query": "",
            "chart_attributes": {},
            "data": "{}",
            "chart_json": "{}",
            "insights": "",
            "error": "",
            "retry_count": 0,
        }
    )

async def run_agent():
    print("Frammer Analytics Agent")
    print("Type 'quit' to exit.\n")

    loop = asyncio.get_event_loop()

    while True:
        user_query = (
            await loop.run_in_executor(None, input, "Ask about the Frammer data: ")
        ).strip()

        if user_query.lower() == "quit":
            print("Goodbye!")
            break

        if not user_query:
            continue

        print("\nProcessing...\n")
        try:
            result = await build_and_run_pipeline(user_query)

            print("Insights:")
            print(result["insights"])

            chart = result.get("chart_json", "")
            if chart and chart.strip().startswith("<?xml"):
                chart_path = "chart_output.xml"
                with open(chart_path, "w", encoding="utf-8") as f:
                    f.write(chart)
                print(f"\n Dashboard XML saved to {chart_path}")
            elif chart and chart not in ("{}", ""):
                try:
                    chart_obj = json.loads(chart)
                    if "error" in chart_obj:
                        print(f"\n Chart warning: {chart_obj['error']}")
                except json.JSONDecodeError:
                    pass 

        except Exception as exc:
            print(f"\n Agent error: {exc}")

        print()


if __name__ == "__main__":
    asyncio.run(run_agent())
