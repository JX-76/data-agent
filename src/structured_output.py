"""Prompt + JSON Schema: Structured LLM outputs with schema validation.

Inspired by Spring AI's BeanOutputConverter.getFormat().
Each LLM call includes a JSON schema in the prompt, and outputs are wrapped
in TextType markers for streaming parse.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable


# ── TextType Markers ──

TEXT_TYPE_MARKERS = {
    "json": ("<JSON>", "</JSON>"),
    "sql": ("<SQL>", "</SQL>"),
    "python": ("<PYTHON>", "</PYTHON>"),
    "plan": ("<PLAN>", "</PLAN>"),
    "report": ("<REPORT>", "</REPORT>"),
}


class TextTypeParser:
    """Parser for TextType-marked content."""
    
    @staticmethod
    def extract(text: str, text_type: str) -> str | None:
        """Extract content between TextType markers.
        
        Args:
            text: Raw text with markers
            text_type: Type of content (json, sql, python, etc.)
        
        Returns:
            Extracted content or None if not found
        """
        start_marker, end_marker = TEXT_TYPE_MARKERS.get(text_type, ("", ""))
        if not start_marker or not end_marker:
            return None
        
        pattern = f"{re.escape(start_marker)}(.*?){re.escape(end_marker)}"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return None
    
    @staticmethod
    def wrap(content: str, text_type: str) -> str:
        """Wrap content in TextType markers.
        
        Args:
            content: Content to wrap
            text_type: Type of content
        
        Returns:
            Wrapped content
        """
        start_marker, end_marker = TEXT_TYPE_MARKERS.get(text_type, ("", ""))
        return f"{start_marker}\n{content}\n{end_marker}"
    
    @staticmethod
    def parse_streaming(text: str, text_type: str) -> list[dict[str, Any]]:
        """Parse streaming text with TextType markers.
        
        Returns a list of parsed chunks as they become available.
        
        Args:
            text: Streaming text
            text_type: Expected content type
        
        Returns:
            List of parsed chunks
        """
        chunks = []
        start_marker, end_marker = TEXT_TYPE_MARKERS.get(text_type, ("", ""))
        
        if not start_marker or not end_marker:
            return chunks
        
        # Find all complete blocks
        pattern = f"{re.escape(start_marker)}(.*?){re.escape(end_marker)}"
        for match in re.finditer(pattern, text, re.DOTALL):
            content = match.group(1).strip()
            try:
                if text_type == "json":
                    parsed = json.loads(content)
                elif text_type == "sql":
                    parsed = {"sql": content}
                elif text_type == "python":
                    parsed = {"code": content}
                else:
                    parsed = {"content": content}
                chunks.append(parsed)
            except json.JSONDecodeError:
                chunks.append({"raw": content, "error": "parse_failed"})
        
        return chunks


# ── JSON Schema Builder ──

def build_json_schema(name: str, fields: dict[str, str]) -> dict[str, Any]:
    """Build a JSON schema for structured output.
    
    Args:
        name: Schema name
        fields: Field definitions {field_name: field_type}
            Types: string, number, integer, boolean, array, object
    
    Returns:
        JSON schema dict
    """
    properties = {}
    required = []
    
    for field_name, field_type in fields.items():
        if field_type.startswith("?"):
            # Optional field
            field_type = field_type[1:]
        else:
            required.append(field_name)
        
        if field_type == "string":
            properties[field_name] = {"type": "string"}
        elif field_type == "number":
            properties[field_name] = {"type": "number"}
        elif field_type == "integer":
            properties[field_name] = {"type": "integer"}
        elif field_type == "boolean":
            properties[field_name] = {"type": "boolean"}
        elif field_type == "array":
            properties[field_name] = {"type": "array"}
        elif field_type == "object":
            properties[field_name] = {"type": "object"}
        else:
            properties[field_name] = {"type": "string"}
    
    return {
        "name": name,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def schema_to_prompt(schema: dict[str, Any]) -> str:
    """Convert JSON schema to prompt text.
    
    Args:
        schema: JSON schema dict
    
    Returns:
        Prompt text with schema
    """
    schema_json = json.dumps(schema["schema"], indent=2, ensure_ascii=False)
    
    return f"""请根据以下 JSON Schema 生成输出：

Schema 名称: {schema['name']}

{schema_json}

请确保输出符合上述 schema，并用 <JSON> 和 </JSON> 包裹。
"""


# ── Prompt Builder ──

class StructuredPrompt:
    """Builder for structured prompts with JSON schema."""
    
    def __init__(self, system_prompt: str = ""):
        self.system_prompt = system_prompt
        self.schemas: list[dict[str, Any]] = []
        self.examples: list[dict[str, Any]] = []
    
    def add_schema(self, schema: dict[str, Any]) -> "StructuredPrompt":
        """Add a JSON schema to the prompt."""
        self.schemas.append(schema)
        return self
    
    def add_example(self, input_text: str, output_text: str) -> "StructuredPrompt":
        """Add an example to the prompt."""
        self.examples.append({"input": input_text, "output": output_text})
        return self
    
    def build(self, user_query: str) -> str:
        """Build the complete prompt.
        
        Args:
            user_query: User's query
        
        Returns:
            Complete prompt text
        """
        parts = []
        
        if self.system_prompt:
            parts.append(f"System: {self.system_prompt}")
        
        if self.schemas:
            parts.append("\n输出格式要求：")
            for schema in self.schemas:
                parts.append(schema_to_prompt(schema))
        
        if self.examples:
            parts.append("\n示例：")
            for i, example in enumerate(self.examples, 1):
                parts.append(f"\n示例 {i}:")
                parts.append(f"输入: {example['input']}")
                parts.append(f"输出: {example['output']}")
        
        parts.append(f"\n用户查询: {user_query}")
        parts.append("\n请生成符合要求的输出：")
        
        return "\n".join(parts)


# ── Validation ──

def validate_json_output(output: str, schema: dict[str, Any]) -> tuple[bool, str]:
    """Validate JSON output against schema.
    
    Args:
        output: JSON string
        schema: JSON schema
    
    Returns:
        (is_valid, error_message)
    """
    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"
    
    # Check required fields
    required = schema.get("schema", {}).get("required", [])
    for field in required:
        if field not in data:
            return False, f"Missing required field: {field}"
    
    # Check field types
    properties = schema.get("schema", {}).get("properties", {})
    for field, field_def in properties.items():
        if field in data:
            expected_type = field_def.get("type")
            actual_value = data[field]
            
            if expected_type == "string" and not isinstance(actual_value, str):
                return False, f"Field {field} should be string, got {type(actual_value)}"
            elif expected_type == "number" and not isinstance(actual_value, (int, float)):
                return False, f"Field {field} should be number, got {type(actual_value)}"
            elif expected_type == "integer" and not isinstance(actual_value, int):
                return False, f"Field {field} should be integer, got {type(actual_value)}"
            elif expected_type == "boolean" and not isinstance(actual_value, bool):
                return False, f"Field {field} should be boolean, got {type(actual_value)}"
    
    return True, ""


# ── Convenience Functions ──

def build_plan_schema() -> dict[str, Any]:
    """Build schema for plan output."""
    return build_json_schema("Plan", {
        "thought_process": "string",
        "execution_plan": "array",
    })


def build_sql_schema() -> dict[str, Any]:
    """Build schema for SQL output."""
    return build_json_schema("SQL", {
        "sql": "string",
        "explanation": "?string",
    })


def build_report_schema() -> dict[str, Any]:
    """Build schema for report output."""
    return build_json_schema("Report", {
        "summary": "string",
        "insights": "array",
        "recommendations": "?array",
    })
