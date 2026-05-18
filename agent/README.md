# Multi-Chart Analytics Agent

An autonomous agent that ingests a natural-language business question, retrieves database schema and metric definitions, generates multiple optimized SQL queries, executes them, and finally outputs a structured XML layout describing a dashboard with multiple chart widgets.

## Features

- **LLM-driven Orchestration**: Uses Groq's Llama 3.3 70B model for planning and coordination.
- **Multi-Chart Generation**: Decomposes complex questions into multiple sub-questions and generates a separate chart specification for each.
- **Schema-Aware SQL**: Strictly adheres to the provided database schema, using LangChain tools to introspect tables and metrics.
- **Automatic Retry**: Automatically retries SQL generation up to 3 times when execution errors occur.
- **Dashboard XML Output**: Generates a production-ready XML layout following the Frammer dashboard schema, supporting multiple chart types (bar, line, pie) and KPI widgets.
- **Async Processing**: Built on LangGraph's async runtime for efficient pipeline execution.

## Prerequisites

- Python 3.8+
- PostgreSQL database

## Setup

1.  **Install Dependencies**

```bash
pip install -r requirements.txt
```

2.  **Environment Variables**

Create a `.env` file with the following database credentials:

```env
POSTGRES_HOST=your-db-host
POSTGRES_PORT=5432
POSTGRES_DB=your-db-name
POSTGRES_USER=your-db-user
POSTGRES_PASSWORD=your-db-password
POSTGRES_SSLMODE=require

GROQ_API_KEY=your-groq-api-key
```

## Usage

Run the agent with a question:

```bash
python run.py "What is the total uploaded, created, and published count for the month of January 2026?"
```

## Project Structure

```
agent/
├── tools/                # Tools for SQL execution, schema retrieval, metric definitions
│   ├── __init__.py
│   ├── chart.py          # Chart generation and dashboard XML creation
│   ├── database.py       # Database connection and query execution
│   ├── metric_definitions.py  # Business metric definitions
│   └── schema.py         # Database schema retrieval
├── state.py              # Agent state definition
├── orchestrator.py       # Main LLM orchestration logic
└── run.py                # Entry point for running the agent
```

## Tools

The agent uses the following tools for autonomous operation:

- `get_frammer_schema()`: Retrieves the PostgreSQL database schema.
- `retrieve_metric_definitions()`: Fetches business metric definitions.
- `execute_sql_query()`: Executes SQL queries and returns structured results.
- `generate_plotly_chart()`: Generates chart XML based on data and attributes.

## Development

### Adding New Metrics

To add new business metrics, update the `METRIC_DICTIONARY` in `tools/metric_definitions.py`:

```python
METRIC_DICTIONARY: dict[str, str] = {
    "metric_name": "Description of the metric and relevant columns",
    # ...
}
```

### SQL Query Validation

SQL queries are validated against write operations using word-boundary regex. This prevents accidental data modification.

### Chart XML Format

The agent generates XML following this structure:

```xml
<dashboard version="1.0" theme="light" cols="12">
    <meta>
        <title>Dashboard Title</title>
        <description>...</description>
        <created>YYYY-MM-DD</created>
    </meta>
    <layout>
        <row id="r1" label="Row Title">
            <widget id="w1" type="bar-chart" col="1" span="12" title="Chart Title" ... />
        </row>
    </layout>
</dashboard>
```