import json
import sqlite3
from pathlib import Path


def make_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE singer(id INTEGER PRIMARY KEY, name TEXT, country TEXT, age INTEGER);
    INSERT INTO singer VALUES (1,'Ada','Nigeria',30),(2,'Bo','France',25),(3,'Cy','France',41),(4,'Di','Nigeria',35);
    """)
    conn.commit()
    conn.close()
    return path


class FakeLLM:
    """Plays back scripted assistant messages: str = plain reply, dict = tool call {'sql': ...}."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def chat(self, messages, tools=None, tool_choice=None, max_tokens=1024, temperature=0.0):
        self.calls.append({"messages": list(messages), "tools": tools, "temperature": temperature})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, dict):
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{len(self.calls)}", "type": "function", "function": {"name": "run_sql", "arguments": json.dumps(step)}}]}
        else:
            msg = {"role": "assistant", "content": step}
        return {"message": msg, "model": "fake", "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "cached": False}
