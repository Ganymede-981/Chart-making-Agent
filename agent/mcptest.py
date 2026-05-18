import json

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
load_dotenv()

from tools import (
    execute_sql_query,
    generate_plotly_chart,
    get_frammer_schema,
    retrieve_metric_definitions,
)

mcp = FastMCP("Frammer_Analytics_Server")

@mcp.tool()
def tool_retrieve_metric_definitions(search_term: str) -> str:
    return retrieve_metric_definitions(search_term)


@mcp.tool()
def tool_get_frammer_schema() -> str:
    return get_frammer_schema()


@mcp.tool()
def tool_execute_sql_query(query: str, chart_attributes: str = "{}") -> str:
    try:
        attrs = json.loads(chart_attributes)
    except json.JSONDecodeError:
        attrs = {}
    return execute_sql_query(query, chart_attributes=attrs)


@mcp.tool()
def tool_generate_plotly_chart(query: str) -> str:
    return generate_plotly_chart(query=query)


if __name__ == "__main__":
    print("Starting Frammer Analytics MCP Server")
    mcp.run("streamable-http")