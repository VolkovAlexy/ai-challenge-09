import argparse
import sys
from pathlib import Path

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
    parser.add_argument(
        "--stop",
        action="append",
        metavar="SEQ",
        help="Стоп-последовательность, на которой модель прервёт генерацию. "
        "Можно указать несколько раз: --stop '###' --stop 'User:'.",
    )
    system_group = parser.add_mutually_exclusive_group()
    system_group.add_argument(
        "--system",
        help="Системный промпт (текст).",
    )
    system_group.add_argument(
        "--system-file",
        help="Путь к файлу с системным промптом.",
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


def _run_once(
    client: APIClient,
    prompt: str,
    system: str | None,
    args: argparse.Namespace,
) -> None:
    if args.no_stream:
        _print_response(client.complete(prompt, system, stop=args.stop))
    else:
        for chunk in client.stream(prompt, system, stop=args.stop):
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
            _run_once(client, line, None, args)
        except ClientError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)


def _load_system(args: argparse.Namespace) -> str | None:
    if not args.system_file:
        return args.system
    try:
        return Path(args.system_file).read_text().strip()
    except OSError as exc:
        raise ConfigError(f"Не удалось прочитать {args.system_file}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _load_settings(args)
        system = _load_system(args)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 1

    with APIClient(settings) as client:
        try:
            if args.prompt:
                _run_once(client, args.prompt, system, args)
            else:
                _run_repl(client, args)
        except ClientError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
