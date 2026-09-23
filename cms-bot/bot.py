import argparse
import importlib
import sys

from matmuhbot import state
from matmuhbot.api import AuthError
from matmuhbot.config import MissingSecret
from matmuhbot.llm import LlmError
from matmuhbot.merge import MissingSchema

STAGE_MODULES = {
    0: "matmuhbot.stages.s0_prepare",
    1: "matmuhbot.stages.s1_terms",
    2: "matmuhbot.stages.s2_staff",
    3: "matmuhbot.stages.s3_lectures",
    4: "matmuhbot.stages.s4_groups",
    5: "matmuhbot.stages.s5_offerings",
    6: "matmuhbot.stages.s6_statistics",
    7: "matmuhbot.stages.s7_announcements",
    8: "matmuhbot.stages.s8_news",
    9: "matmuhbot.stages.s9_translate",
}


def cmd_status(_args) -> int:
    current = state.load()
    for number, (key, label, deps) in state.STAGES.items():
        info = current["steps"].get(key, {})
        status = info.get("status", "-")
        when = info.get("finishedAt", "")
        need = ", ".join(map(str, deps)) or "-"
        print(f"  {number:>2}  {label:<42} {status:<8} {when:<20} ön koşul: {need}")
    return 0


def cmd_inbox(_args) -> int:
    from matmuhbot import inbox

    files = inbox.scan()
    if not files:
        print("temp/ boş.")
    for f in files:
        label = inbox.KINDS.get(f.kind, "tanınmadı")
        print(f"  {f.name:<70} {label}")
    return 0


def cmd_models(_args) -> int:
    from matmuhbot import llm

    try:
        names = llm.list_models()
    except MissingSecret as error:
        print(f"Anahtar eksik: {error}")
        return 1
    for name in names:
        print(f"  {name}")
    return 0


def cmd_run(args) -> int:
    number = args.stage
    if number not in state.STAGES:
        print(f"Bilinmeyen aşama: {number}")
        return 2
    missing = state.missing_prerequisites(state.load(), number)
    if missing and not args.force:
        print(f"Aşama {number} başlamıyor; önce bitmesi gereken: {', '.join(map(str, missing))}")
        return 1
    module_name = STAGE_MODULES.get(number)
    if not module_name:
        print(f"Aşama {number} ({state.STAGES[number][1]}) henüz yazılmadı.")
        return 1
    module = importlib.import_module(module_name)
    phase = getattr(module, args.phase, None) if args.phase else module.run
    if phase is None:
        print(f"Aşama {number} için '{args.phase}' fazı yok.")
        return 1
    try:
        phase(dry_run=args.dry_run)
    except MissingSecret as error:
        print(f"Anahtar eksik: {error}")
        return 1
    except (FileNotFoundError, LlmError, MissingSchema) as error:
        print(f"Durdu: {error}")
        return 1
    except AuthError as error:
        print(f"Yetki reddedildi ({error.status}); durdu. Anahtarı kontrol edin, sonra aynı komutu tekrar çalıştırın.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="bot.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("inbox").set_defaults(func=cmd_inbox)
    sub.add_parser("models").set_defaults(func=cmd_models)
    run = sub.add_parser("run")
    run.add_argument("stage", type=int)
    run.add_argument("phase", nargs="?", choices=["scrape", "normalize", "push"])
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--force", action="store_true")
    run.set_defaults(func=cmd_run)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
