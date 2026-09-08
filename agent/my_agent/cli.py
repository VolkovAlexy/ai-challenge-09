"""Entry point: typer-аргументы, проверка конфига/промпта, запуск Textual App."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from my_agent.config.schema import Config
from my_agent.config.store import ConfigError, load_config
from my_agent.llm.client import LLMClient
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.app import AgentApp

DEFAULT_SYSTEM_PROMPT_PATH = Path("SYSTEM_PROMPT.md")


def _fail(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


main = typer.Typer(help="TUI-чат с LLM по OpenAI-compatible API")


@main.command()
def chat(
    config: Annotated[
        Path, typer.Option("--config", help="Путь к config.json")
    ] = Path("config.json"),
    system_prompt: Annotated[
        Path | None, typer.Option("--system-prompt", help="Системный промпт стартового агента")
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="Модель стартового агента (provider:model)")
    ] = None,
) -> None:
    """Запускает TUI-чат с LLM (OpenAI-compatible API)."""
    try:
        cfg: Config = load_config(config)
    except ConfigError as exc:
        _fail(f"Ошибка конфигурации:\n{exc}")

    default_prompt_path = DEFAULT_SYSTEM_PROMPT_PATH
    if not default_prompt_path.exists():
        _fail(
            f"Системный промпт не найден: {default_prompt_path.resolve()}\n"
            "Создайте файл с системным промптом в корне проекта."
        )
    default_prompt = default_prompt_path.read_text(encoding="utf-8")

    startup_prompt = default_prompt
    if system_prompt is not None:
        if not system_prompt.exists():
            _fail(f"Системный промпт не найден: {system_prompt.resolve()}")
        startup_prompt = system_prompt.read_text(encoding="utf-8")

    startup_model = None
    if model is not None:
        try:
            cfg.resolve_model(model)
        except ValueError as exc:
            known = ", ".join(cfg.all_model_ids())
            _fail(f"Ошибка --model: {exc}\nДоступные модели: {known}")
        startup_model = model

    llm = LLMClient()
    try:
        AgentApp(
            config=cfg,
            llm=llm,
            tools=ToolRegistry(),
            default_system_prompt=default_prompt,
            startup_system_prompt=startup_prompt,
            startup_model=startup_model,
        ).run()
    finally:
        asyncio.run(llm.close())


if __name__ == "__main__":
    main()
