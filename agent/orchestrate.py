import asyncio
import json
import os
from datetime import date
from typing import Any, Dict, List, Optional, TypedDict
import xml.etree.ElementTree as ET

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

llm_orchestrator = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
llm_analyst = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.4)

class AgentState(TypedDict):
    """
    Shared state dictionary for the multi-chart orchestration pipeline.

    Attributes:
        question:         The original natural-language question from the user.
        semantic_context: Matched metric definitions retrieved from the dictionary.
        schema:           Live PostgreSQL schema string fetched at runtime.
        chart_specs:      List of per-chart spec dicts, each enriched with
                          ``sql_query``, ``records``, ``chart_attrs``, and ``chart_xml``
                          after ``generate_all_charts`` runs.
        chart_json:       Merged dashboard XML string (or ``"{}"`` on failure).
        error:            Pipeline-level error message (empty string if none).
    """
    question: str
    semantic_context: str
    schema: str
    chart_specs: List[Dict]
    chart_json: str
    error: str


def _merge_dashboard_xmls(xml_strings: List[str], title: str = "Dashboard") -> str:
    """
    Merge multiple single-chart dashboard XML strings into one combined dashboard.

    Each input XML is expected to follow the ``<dashboard><layout><row>...`` schema
    produced by ``_build_dashboard_xml``.  Row ``id`` attributes are reassigned
    sequentially to avoid collisions in the merged output.

    Args:
        xml_strings: List of XML strings (may include empty or non-XML values,
                     which are silently skipped).
        title:       Title written into the merged dashboard ``<meta>`` block.

    Returns:
        A well-formed XML string beginning with ``<?xml version="1.0" ...?>``.
    """
    root = ET.Element("dashboard", {"version": "1.0", "theme": "light", "cols": "12"})
    meta = ET.SubElement(root, "meta")
    ET.SubElement(meta, "title").text = title
    ET.SubElement(meta, "description").text = "Auto-generated multi-chart analytics dashboard"
    ET.SubElement(meta, "created").text = date.today().isoformat()

    layout = ET.SubElement(root, "layout")
    row_counter = 1

    for xml_str in xml_strings:
        if not xml_str or not xml_str.strip().startswith("<?xml"):
            continue
        try:
            clean = xml_str.split("?>", 1)[-1].strip()
            chart_root = ET.fromstring(clean)
            chart_layout = chart_root.find("layout")
            if chart_layout is not None:
                for row in list(chart_layout):
                    row.set("id", f"r{row_counter}")
                    row_counter += 1
                    layout.append(row)
        except ET.ParseError:
            continue

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _generate_sql_for_spec(sub_question: str, schema: str, context: str, error: str = "") -> str:
    """
    Ask the LLM to generate a PostgreSQL SELECT query for a single chart spec.

    Args:
        sub_question: The specific data question this chart needs to answer.
        schema:       Full database schema string (table names + column types).
        context:      Relevant metric definitions from the semantic dictionary.
        error:        Previous SQL execution error to feed back for self-correction
                      (empty string on the first attempt).

    Returns:
        A raw SQL SELECT query string with no markdown fences.
    """
    prompt = PromptTemplate.from_template(
        "Database Schema:\n{schema}\n\n"
        "Business Metric Definitions: {context}\n\n"
        "Previous Error (fix this if present): {error}\n\n"
        "Question: {question}\n\n"
        "Write a valid PostgreSQL SELECT query to answer the question.\n"
        "CRITICAL RULES:\n"
        "1. ONLY use EXACT table and column names from the Database Schema above.\n"
        "2. DO NOT guess table names. Do not drop plurals "
        "(e.g. use 'channel_metrics', NOT 'channel_metric').\n"
        "3. Return ONLY the raw SQL query string — no markdown fences, no formatting, no explanation."
    )
    raw = llm_orchestrator.invoke(
        prompt.format(schema=schema, context=context, error=error, question=sub_question)
    ).content
    return raw.strip().strip("```sql").strip("```").strip()


def _generate_chart_attrs(sql: str, sub_question: str, records: List[Dict]) -> Dict:
    """
    Ask the LLM to choose the best chart type and axis columns for a result set.

    Provides the first three rows as a sample to help the model pick valid,
    existing column names.  Falls back to an empty dict on JSON parse failure.

    Args:
        sql:          The SQL query that produced the data.
        sub_question: The user-facing question this chart answers.
        records:      Full list of result-row dicts from the database.

    Returns:
        A dict with keys ``type``, ``x_axis``, ``y_axis``, and ``title``;
        empty dict if the LLM response cannot be parsed.
    """
    sample_rows = records[:3]
    col_names = list(sample_rows[0].keys()) if sample_rows else []

    col_hint = (
        f"Available columns in the result: {col_names}\n"
        f"Sample rows: {json.dumps(sample_rows, default=str)}\n\n"
        if col_names else ""
    )

    prompt = PromptTemplate.from_template(
        "You are a data visualisation expert.\n\n"
        "SQL query that produced the data:\n{sql}\n\n"
        "{col_hint}"
        "User question: {question}\n\n"
        "Choose the best chart type and identify the right columns.\n"
        "Respond with a single valid JSON object (no markdown, no extra text) "
        "with EXACTLY these keys:\n"
        "  type    -- one of: bar, line, pie\n"
        "            Use 'line' for time-series or trend data (x-axis is a date/month/period).\n"
        "            Use 'pie' when showing proportions or share of a total (few categories).\n"
        "            Use 'bar' for comparisons across categories (default).\n"
        "  x_axis  -- EXACT column name for the X axis (must be one of the available columns)\n"
        "  y_axis  -- EXACT column name(s) for the Y axis, comma-separated if multiple\n"
        "             (must be numeric columns from the available columns)\n"
        "  title   -- a short, descriptive chart title (max 8 words)\n\n"
        "Return ONLY the JSON object."
    )
    raw = llm_orchestrator.invoke(
        prompt.format(sql=sql, question=sub_question, col_hint=col_hint)
    ).content.strip().strip("```json").strip("```").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _process_single_spec(spec: Dict, schema: str, context: str) -> Dict:
    """
    Run the full SQL -> execute -> chart-attrs -> chart-xml pipeline for one spec.
    Retries SQL generation up to 3 times on execution error.
    Returns the input spec dict enriched with 'sql_query', 'records', and 'chart_xml'.
    """
    sub_question = spec.get("sub_question", "")
    spec_error = ""

    for _attempt in range(3):
        sql = _generate_sql_for_spec(sub_question, schema, context, error=spec_error)
        raw_result = execute_sql_query(sql, chart_attributes={})
        parsed = json.loads(raw_result)

        if "error" in parsed:
            spec_error = parsed["error"]
            continue

        records = parsed.get("data", [])
        attrs = _generate_chart_attrs(sql, sub_question, records)

        if not attrs.get("type") and spec.get("chart_type_hint"):
            attrs["type"] = spec["chart_type_hint"]

        chart_xml = generate_plotly_chart(data_records=records, chart_attributes=attrs) if records else ""

        return {**spec, "sql_query": sql, "records": records, "chart_attrs": attrs, "chart_xml": chart_xml}

    return {**spec, "sql_query": "", "records": [], "chart_attrs": {}, "chart_xml": "", "sql_error": spec_error}

async def build_and_run_pipeline(question: str) -> AgentState:
    """
    Build and execute the multi-chart orchestration LangGraph pipeline.

    Decomposes the user question into up to three chart specs, processes each
    spec in parallel (SQL generation → execution → chart XML), merges the
    resulting XMLs into a single dashboard, and generates cross-chart insights.

    Pipeline sequence:

        retrieve_context → get_schema → decompose_question
            → generate_all_charts → merge_xml → generate_insights

    Args:
        question: The natural-language analytics question from the user.

    Returns:
        The final ``AgentState`` dict containing ``insights``, ``chart_json``
        (merged dashboard XML), ``chart_specs`` (per-chart detail), and all
        intermediate fields.
    """
    def retrieve_context(state: AgentState):
        result = retrieve_metric_definitions(state["question"])
        return {"semantic_context": result}

    def get_schema(state: AgentState):
        result = get_frammer_schema()
        return {"schema": result}

    def decompose_question(state: AgentState):
        prompt = PromptTemplate.from_template(
            "You are a data analytics orchestrator for a media analytics platform.\n\n"
            "Database Schema (overview):\n{schema}\n\n"
            "Business Metric Definitions:\n{context}\n\n"
            "User question: {question}\n\n"
            "Decide how many charts are needed to answer this question.\n"
            "IMPORTANT RULES:\n"
            "- DEFAULT to 1 chart. Only return more than 1 if the question EXPLICITLY asks for "
            "  multiple unrelated views (e.g. 'show me X AND ALSO Y by Z').\n"
            "- Questions like 'show me X vs Y' or 'compare A and B' are STILL 1 chart "
            "  (multi-series on the same chart).\n"
            "- Never return more than 3 charts.\n"
            "- Do NOT generate overlapping or redundant charts.\n\n"
            "Respond with a JSON array of objects. Each object must have exactly:\n"
            "  sub_question    -- the specific data question for this chart (full sentence)\n"
            "  chart_type_hint -- one of: bar, line, pie\n\n"
            "Return ONLY the JSON array, no markdown, no extra text.\n\n"
            'Example (single chart): [{{"sub_question": "Total sessions by channel?", "chart_type_hint": "bar"}}]\n'
            'Example (two charts only when both EXPLICITLY requested): '
            '[{{"sub_question": "Revenue by region?", "chart_type_hint": "bar"}}, '
            '{{"sub_question": "Revenue trend over time?", "chart_type_hint": "line"}}]'
        )
        raw = llm_orchestrator.invoke(
            prompt.format(
                schema=state.get("schema", ""),
                context=state.get("semantic_context", ""),
                question=state["question"],
            )
        ).content.strip().strip("```json").strip("```").strip()

        try:
            specs = json.loads(raw)
            if not isinstance(specs, list) or not specs:
                raise ValueError("Empty or non-list response")
            specs = specs[:3]
        except (json.JSONDecodeError, ValueError):
            specs = [{"sub_question": state["question"], "chart_type_hint": "bar"}]

        return {"chart_specs": specs}

    async def generate_all_charts(state: AgentState):
        specs  = state.get("chart_specs", [])
        schema  = state.get("schema", "")
        context = state.get("semantic_context", "")

        if not specs:
            return {"chart_specs": [], "error": "No chart specs were produced."}

        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, _process_single_spec, spec, schema, context)
            for spec in specs
        ]
        completed = await asyncio.gather(*tasks)
        return {"chart_specs": list(completed)}

    def merge_xml(state: AgentState):
        specs = state.get("chart_specs", [])
        xml_strings = [s.get("chart_xml", "") for s in specs]
        valid_xmls  = [x for x in xml_strings if x and x.strip().startswith("<?xml")]

        if not valid_xmls:
            return {"chart_json": "{}"}

        merged = _merge_dashboard_xmls(valid_xmls, title=state.get("question", "Dashboard"))
        return {"chart_json": merged}

    def generate_insights(state: AgentState):
        specs = state.get("chart_specs", [])

        all_data = [
            {"chart": s.get("sub_question", ""), "data": s.get("records", [])[:20]}
            for s in specs
            if s.get("records")
        ]

        if not all_data:
            return {"insights": "No data was returned, cannot generate insights."}

        data_str = json.dumps(all_data, indent=2, default=str)

        prompt = PromptTemplate.from_template(
            "You are a business analyst for a media analytics platform.\n"
            "The user's overall question was: {question}\n\n"
            "Data retrieved across {n} chart(s):\n{data}\n\n"
            "Provide 3-5 concise, actionable bullet-point insights that directly answer the user's "
            "question. Synthesise across all charts where relevant. "
            "Focus on key trends, comparisons, or anomalies. "
            "Use business language only. No code, no SQL."
        )
        insights = llm_analyst.invoke(
            prompt.format(
                question=state.get("question", ""),
                n=len(all_data),
                data=data_str,
            )
        ).content
        return {"insights": insights}

    def handle_error(state: AgentState):
        return {
            "insights": f"Pipeline error: {state.get('error', 'Unknown error')}",
            "chart_json": "{}",
        }

    def route_after_decompose(state: AgentState) -> str:
        return "handle_error" if not state.get("chart_specs") else "generate_all_charts"

    workflow = StateGraph(AgentState)

    workflow.add_node("retrieve_context", retrieve_context)
    workflow.add_node("get_schema", get_schema)
    workflow.add_node("decompose_question", decompose_question)
    workflow.add_node("generate_all_charts", generate_all_charts)
    workflow.add_node("merge_xml", merge_xml)
    workflow.add_node("generate_insights", generate_insights)
    workflow.add_node("handle_error", handle_error)

    workflow.set_entry_point("retrieve_context")
    workflow.add_edge("retrieve_context", "get_schema")
    workflow.add_edge("get_schema", "decompose_question")
    workflow.add_conditional_edges(
        "decompose_question",
        route_after_decompose,
        {"generate_all_charts": "generate_all_charts", "handle_error": "handle_error"},
    )
    workflow.add_edge("generate_all_charts", "merge_xml")
    workflow.add_edge("merge_xml", "generate_insights")
    workflow.add_edge("generate_insights", END)
    workflow.add_edge("handle_error", END)

    app = workflow.compile()

    return await app.ainvoke(
        {
            "question": question,
            "semantic_context": "",
            "schema": "",
            "chart_specs": [],
            "chart_json": "{}",
            "insights": "",
            "error": "",
        }
    )

async def run_agent():
    """
    Interactive CLI entry point for the multi-chart orchestration agent.

    Runs an async read-eval-print loop that accepts natural-language questions,
    passes them to ``build_and_run_pipeline``, reports per-chart status, prints
    the synthesised insights, and saves the merged dashboard XML to
    ``chart_output.xml``.  Type ``quit`` to exit.
    """
    print("Orchestrated Multi-Chart Analytics Agent")
    print("Tools loaded from: tools/")
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

        print("\nProcessing (Multi-Chart Parallel)\n")
        try:
            result = await build_and_run_pipeline(user_query)
            specs = result.get("chart_specs", [])
            print(f"Charts generated: {len(specs)}")
            for i, s in enumerate(specs, 1):
                ok = s.get("chart_xml", "").startswith("<?xml")
                status = "OK" if ok else "FAILED"
                print(f"  [{status}] Chart {i}: {s.get('sub_question', '')}")
                if not ok and s.get("sql_error"):
                    print(f"Error: {s['sql_error']}")

            print("\nInsights:")
            print(result["insights"])

            chart = result.get("chart_json", "")
            if chart and chart.strip().startswith("<?xml"):
                chart_path = "chart_output.xml"
                with open(chart_path, "w", encoding="utf-8") as f:
                    f.write(chart)
                print(f"\nDashboard XML saved to {chart_path}")
            elif chart and chart not in ("{}", ""):
                try:
                    chart_obj = json.loads(chart)
                    if "error" in chart_obj:
                        print(f"\nChart warning: {chart_obj['error']}")
                except json.JSONDecodeError:
                    pass

        except Exception as exc:
            print(f"\nAgent error: {exc}")

        print()


if __name__ == "__main__":
    asyncio.run(run_agent())
