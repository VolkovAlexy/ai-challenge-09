import argparse
import sys

from .client import APIClient
from .config import Settings
from .errors import ClientError, ConfigError
from .models import ChatResponse

HELP_TEXT = """\
Команды интерактивного режима:
  /help          показать эту справку
  /quit, /exit   выйти (или Ctrl+D)
  /model NAME    сменить модель
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="one-question",
        description="Простой клиент для OpenAI-compatible API.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Текст запроса. Если не задан, запускается интерактивный режим.",
    )
    parser.add_argument(
        "--model",
        help="Модель (переопределяет API_MODEL).",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Не стримить: вывести полный ответ целиком.",
    )
    return parser


def _load_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if args.model:
        settings = Settings(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=args.model,
            chat_path=settings.chat_path,
        )
    return settings


def _print_response(response: ChatResponse) -> None:
    sys.stdout.write(response.content)
    sys.stdout.write("\n\n")


def _run_once(client: APIClient, prompt: str, args: argparse.Namespace) -> None:
    if args.no_stream:
        _print_response(client.complete(prompt))
    else:
        for chunk in client.stream(prompt):
            sys.stdout.write(chunk)
            sys.stdout.flush()
        sys.stdout.write("\n")


def _run_repl(client: APIClient, args: argparse.Namespace) -> None:
    endpoint = f"{client.settings.base_url}{client.settings.chat_path}"
    print(f"Endpoint: {endpoint}")
    print(f"Модель: {client.settings.model}. Введите запрос, /help или /quit.")
    while True:
        try:
            line = input(">>> ").strip()
        except EOFError:
            print()
            return
        if not line or line.startswith("#"):
            continue
        if line in ("/quit", "/exit"):
            return
        if line == "/help":
            print(HELP_TEXT)
            continue
        if line.startswith("/model "):
            client.settings = Settings(
                base_url=client.settings.base_url,
                api_key=client.settings.api_key,
                model=line.removeprefix("/model ").strip(),
                chat_path=client.settings.chat_path,
            )
            print(f"Модель: {client.settings.model}")
            continue
        if line.startswith("/"):
            print(f"Неизвестная команда: {line}")
            continue
        try:
            _run_once(client, line, args)
        except ClientError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _load_settings(args)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 1

    with APIClient(settings) as client:
        try:
            if args.prompt:
                _run_once(client, args.prompt, args)
            else:
                _run_repl(client, args)
        except ClientError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
