"""Совместимость: вызов Claude переехал в `meet.llm.claude`.

Здесь — прежние имена для `meet assist` и его тестов.
"""

from meet.llm.base import AgentReply
from meet.llm.claude import (
    ALL_TOOLS_DENIED,
    READ_TOOLS,
    check_auth,
    find_cli,
    make_permission_callback,
)
from meet.llm.claude import run as run_agent_query

__all__ = [
    "ALL_TOOLS_DENIED", "READ_TOOLS", "AgentReply", "check_auth", "find_cli",
    "make_permission_callback", "run_agent_query",
]
