"""Code Executor Agent using LangGraph.

This agent uses a stateful LangGraph workflow to:
- Receive natural language questions about Excel data
- Write Python/Pandas code to answer the question
- Execute the code safely and return results
- Handle errors and retry with corrections
"""

from __future__ import annotations

import io
import re
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Annotated, TypedDict

import numpy as np
import pandas as pd
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

from core.agents.base import AgentResult, BaseAgent, get_llm_client
from core.agents.mapper import WorkbookSchema, schema_to_dict
from core.config import settings
from core.logging import get_logger

logger = get_logger(__name__)


CODE_GENERATION_PROMPT = """You are a Python data analyst expert. Your task is to write Python code to answer questions about Excel data.

CRITICAL RULES:
1. DO NOT use any import statements - libraries are already available
2. NEVER perform calculations yourself - ALWAYS write Python code to do it
3. The file path is already available as `file_path` variable
4. Store your final answer in a variable called `result`
5. Keep code simple and focused on answering the specific question
6. Handle potential errors gracefully (missing data, type issues)
7. For monetary values, format appropriately
8. DO NOT use print() - just assign to `result`

AVAILABLE VARIABLES (already imported/defined - DO NOT import these):
- `pd` - pandas library
- `np` - numpy library
- `datetime`, `date`, `timedelta` - datetime utilities
- `file_path` - path to the Excel file

AVAILABLE DATA:
- File path: {file_path}
- Workbook schema: {schema}

USER QUESTION: {question}

Write Python code that:
1. Loads the specific sheet(s) needed using pd.read_excel(file_path, sheet_name='SheetName')
2. Performs the necessary data operations
3. Stores the final answer in `result`

Respond with ONLY the Python code, no explanations. DO NOT include any import statements.
"""

ERROR_CORRECTION_PROMPT = """The previous code produced an error. Fix the code based on the error message.

ORIGINAL CODE:
```python
{code}
```

ERROR:
{error}

CRITICAL RULES:
1. DO NOT use any import statements - libraries are already available
2. The file path is already available as `file_path` variable
3. Store your final answer in a variable called `result`

AVAILABLE VARIABLES (already imported/defined - DO NOT import these):
- `pd` - pandas library
- `np` - numpy library
- `datetime`, `date`, `timedelta` - datetime utilities
- `file_path` - path to the Excel file

AVAILABLE DATA:
- File path: {file_path}
- Workbook schema: {schema}

USER QUESTION: {question}

Write corrected Python code that fixes the error. Respond with ONLY the Python code. DO NOT include any import statements.
"""


class AgentState(TypedDict):
    """State for the code executor agent."""

    messages: Annotated[list, add_messages]
    question: str
    file_path: str
    schema: dict
    code: str | None
    result: Any
    error: str | None
    iterations: int
    success: bool


@dataclass
class ExecutionResult:
    """Result of code execution."""

    success: bool
    result: Any = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    code_executed: str = ""


class CodeExecutorAgent(BaseAgent):
    """Agent that executes Python code to answer questions about Excel data.

    Uses LangGraph for stateful workflow management with retry capability.
    """

    def __init__(self):
        """Initialize the Code Executor agent."""
        super().__init__(name="CodeExecutorAgent")
        self.max_iterations = settings.AGENT_MAX_ITERATIONS
        self._workflow = self._build_workflow()

    def _build_workflow(self) -> StateGraph:
        """Build the LangGraph workflow."""
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("generate_code", self._generate_code_node)
        workflow.add_node("execute_code", self._execute_code_node)
        workflow.add_node("handle_error", self._handle_error_node)

        # Set entry point
        workflow.set_entry_point("generate_code")

        # Add edges
        workflow.add_edge("generate_code", "execute_code")
        workflow.add_conditional_edges(
            "execute_code",
            self._should_retry,
            {
                "retry": "handle_error",
                "success": END,
                "max_iterations": END,
            },
        )
        workflow.add_edge("handle_error", "generate_code")

        return workflow.compile()

    async def _generate_code_node(self, state: AgentState) -> dict:
        """Generate Python code to answer the question."""
        llm = get_llm_client()

        if state.get("error"):
            # Use error correction prompt
            prompt = ERROR_CORRECTION_PROMPT.format(
                code=state["code"],
                error=state["error"],
                file_path=state["file_path"],
                schema=state["schema"],
                question=state["question"],
            )
        else:
            # Use initial generation prompt
            prompt = CODE_GENERATION_PROMPT.format(
                file_path=state["file_path"],
                schema=state["schema"],
                question=state["question"],
            )

        response = await llm.ainvoke(prompt)
        code = response.content if hasattr(response, "content") else str(response)

        # Clean the code
        code = self._clean_code(code)

        return {"code": code, "error": None}

    async def _execute_code_node(self, state: AgentState) -> dict:
        """Execute the generated Python code."""
        code = state["code"]
        file_path = state["file_path"]

        execution_result = self._safe_execute(code, file_path)

        if execution_result.success:
            return {
                "result": execution_result.result,
                "success": True,
                "iterations": state.get("iterations", 0) + 1,
            }
        else:
            return {
                "error": execution_result.error,
                "success": False,
                "iterations": state.get("iterations", 0) + 1,
            }

    async def _handle_error_node(self, state: AgentState) -> dict:
        """Handle execution errors."""
        self.logger.warning(
            f"Code execution error (iteration {state['iterations']})",
            error=state["error"],
        )
        return {}

    def _should_retry(self, state: AgentState) -> str:
        """Determine if we should retry code generation."""
        if state.get("success"):
            return "success"

        if state.get("iterations", 0) >= self.max_iterations:
            self.logger.error(f"Max iterations ({self.max_iterations}) reached")
            return "max_iterations"

        return "retry"

    def _clean_code(self, code: str) -> str:
        """Clean generated code (remove markdown, imports, etc.)."""
        code = code.strip()

        # Remove markdown code blocks
        if code.startswith("```python"):
            code = code[9:]
        elif code.startswith("```"):
            code = code[3:]

        if code.endswith("```"):
            code = code[:-3]

        # Remove import statements (they will fail in our restricted environment)
        lines = code.split("\n")
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip import and from...import statements
            if stripped.startswith("import ") or stripped.startswith("from "):
                self.logger.debug(f"Stripped import statement: {stripped}")
                continue
            cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    def _safe_execute(self, code: str, file_path: str) -> ExecutionResult:
        """Safely execute Python code in a restricted environment."""
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        # Create a restricted globals dict
        safe_globals = {
            "__builtins__": {
                "len": len,
                "range": range,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "list": list,
                "dict": dict,
                "tuple": tuple,
                "set": set,
                "sum": sum,
                "min": min,
                "max": max,
                "abs": abs,
                "round": round,
                "sorted": sorted,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
                "isinstance": isinstance,
                "type": type,
                "print": print,
                "Exception": Exception,
                "ValueError": ValueError,
                "TypeError": TypeError,
                "KeyError": KeyError,
                "IndexError": IndexError,
                "AttributeError": AttributeError,
                "None": None,
                "True": True,
                "False": False,
                "any": any,
                "all": all,
                "repr": repr,
                "format": format,
                "slice": slice,
                "reversed": reversed,
                "getattr": getattr,
                "hasattr": hasattr,
                "setattr": setattr,
            },
            "pd": pd,
            "np": np,
            "datetime": datetime,
            "date": date,
            "timedelta": timedelta,
            "file_path": file_path,
        }

        local_vars: dict[str, Any] = {}

        try:
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                exec(code, safe_globals, local_vars)

            result = local_vars.get("result", "No result variable set")

            # Convert pandas objects to serializable format
            if isinstance(result, pd.DataFrame):
                result = result.to_dict(orient="records")
            elif isinstance(result, pd.Series):
                result = result.to_dict()

            return ExecutionResult(
                success=True,
                result=result,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                code_executed=code,
            )

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            return ExecutionResult(
                success=False,
                error=error_msg,
                stdout=stdout_capture.getvalue(),
                stderr=stderr_capture.getvalue(),
                code_executed=code,
            )

    async def execute(
        self,
        question: str,
        file_path: str,
        schema: WorkbookSchema | dict,
    ) -> AgentResult:
        """Execute a question against an Excel file.

        Args:
            question: Natural language question about the data.
            file_path: Path to the Excel file.
            schema: Semantic schema of the workbook.

        Returns:
            AgentResult containing the answer or error.
        """
        self._log_start({"question": question, "file_path": file_path})

        try:
            # Convert schema to dict if needed
            if isinstance(schema, WorkbookSchema):
                schema_dict = schema_to_dict(schema)
            else:
                schema_dict = schema

            # Initialize state
            initial_state: AgentState = {
                "messages": [],
                "question": question,
                "file_path": file_path,
                "schema": schema_dict,
                "code": None,
                "result": None,
                "error": None,
                "iterations": 0,
                "success": False,
            }

            # Run the workflow
            final_state = await self._workflow.ainvoke(initial_state)

            if final_state.get("success"):
                result = AgentResult(
                    success=True,
                    data={
                        "answer": final_state["result"],
                        "code_used": final_state.get("code"),
                        "iterations": final_state.get("iterations", 1),
                    },
                    metadata={
                        "question": question,
                        "file_path": file_path,
                    },
                )
            else:
                result = AgentResult(
                    success=False,
                    error=f"Failed to answer question after {final_state.get('iterations', 0)} attempts: {final_state.get('error')}",
                    data={
                        "last_code": final_state.get("code"),
                        "last_error": final_state.get("error"),
                    },
                )

            self._log_complete(result)
            return result

        except Exception as e:
            self._log_error(e)
            return AgentResult(
                success=False,
                error=f"Code executor failed: {str(e)}",
            )
