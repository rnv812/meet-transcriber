"""MCP-сервер Meet для ассистента (0.5): `python -m meet.assist.meet_mcp`.

Claude Code запускает его по `--mcp-config` (сервер `meet`, stdio) — инструменты
`mcp__meet__open_file`, `show_in_folder`, `open_url`, `launch_app`,
`meet_settings`. Что можно в каком ходе — решают ворота Meet (`consent.py`):
по просьбе пользователя — открыть, показать, ссылка; программа — только после
«Разрешить»; из реплик встречи — ничего. Сами проверки — `meet_tools`.
В журнал процесса — ничего из вызова (CLAUDE.md).
"""

from __future__ import annotations

import json

from meet.assist import meet_tools as mt

SERVER = "meet"
TOOLS = ("open_file", "show_in_folder", "open_url", "launch_app", "meet_settings")


def build():
    """MCP-сервер с инструментами Meet (mcp 2.x — `MCPServer`, 1.x — `FastMCP`:
    версию ставит зависимость claude-agent-sdk)."""
    try:
        from mcp.server.mcpserver import MCPServer as Server
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server
        from mcp.server.fastmcp.exceptions import ToolError

    app = Server(SERVER)

    def run(fn, *args):
        # Отказ Meet — текстом для агента (иначе он видит лишь «Error executing tool»).
        try:
            return fn(*args)
        except mt.ToolRefusal as e:
            raise ToolError(f"Meet не выполнил: {e}") from None

    @app.tool()
    def open_file(path: str) -> str:
        """Открыть файл на компьютере пользователя программой по умолчанию (документ, таблицу,
        картинку, текст). path — полный путь. Только когда пользователь об этом попросил."""
        return run(mt.open_file, path)

    @app.tool()
    def show_in_folder(path: str) -> str:
        """Показать файл или папку в Проводнике (Finder) — папка откроется, файл выделен."""
        return run(mt.show_in_folder, path)

    @app.tool()
    def open_url(url: str) -> str:
        """Открыть ссылку http(s) в браузере пользователя."""
        return run(mt.open_url, url)

    @app.tool()
    def launch_app(path: str) -> str:
        """Запустить программу (.exe, .lnk, .app) по полному пути. Meet спросит пользователя
        («Разрешить»); без его согласия не запустится."""
        return run(mt.launch_app, path)

    @app.tool()
    def meet_settings(section: str = "") -> str:
        """Настройки Meet (без секретов) JSON-ом — чтобы ответить, как сейчас настроено.
        section — одна секция (asr, assist, llm, ui, …); пусто — все."""
        return json.dumps(run(mt.settings_view, section or None), ensure_ascii=False, indent=1)

    return app


def mcp_config(python: str) -> dict:
    """Описание сервера для `--mcp-config` Claude Code."""
    return {"mcpServers": {SERVER: {"type": "stdio", "command": python, "args": ["-m", "meet.assist.meet_mcp"]}}}


def main() -> None:
    build().run()


if __name__ == "__main__":
    main()
