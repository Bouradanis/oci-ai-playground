import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from tools.schema import list_tables, describe_table
from tools.query import run_query
from tools.plot import plot_query

app = Server("olist")


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_tables",
            description=(
                "List all Olist tables in the Oracle ADB schema with row counts. "
                "Call this first to orient yourself before writing any SQL."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="describe_table",
            description=(
                "Show column names, data types, nullable flags, and 3 sample rows "
                "for a given table. Use this before querying an unfamiliar table."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Name of the table to describe (case-insensitive)",
                    }
                },
                "required": ["table_name"],
            },
        ),
        types.Tool(
            name="run_query",
            description=(
                "Execute an Oracle SQL query and return results as a markdown table (max 50 rows). "
                "Always use Oracle syntax: FETCH FIRST n ROWS ONLY (not LIMIT), "
                "TO_CHAR(date, 'YYYY-MM') for month grouping, SYSDATE for current date."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "Oracle SQL SELECT statement to execute",
                    }
                },
                "required": ["sql"],
            },
        ),
        types.Tool(
            name="plot_query",
            description=(
                "Execute an Oracle SQL query and render results as a Plotly chart saved to an HTML file. "
                "The query must return at least 2 columns: first column is x-axis / labels, "
                "second column is y-axis / values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "Oracle SQL query returning 2+ columns (x, y)",
                    },
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "scatter", "pie"],
                        "description": "Type of chart to render",
                    },
                },
                "required": ["sql", "chart_type"],
            },
        ),
    ]


@app.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "list_tables":
        result = list_tables()
    elif name == "describe_table":
        result = describe_table(arguments["table_name"])
    elif name == "run_query":
        result = run_query(arguments["sql"])
    elif name == "plot_query":
        result = plot_query(arguments["sql"], arguments["chart_type"])
    else:
        result = f"Unknown tool: {name}"

    return [types.TextContent(type="text", text=result)]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
