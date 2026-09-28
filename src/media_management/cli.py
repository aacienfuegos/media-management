import argparse
import asyncio
import json
import sys
from pathlib import Path

PROCESSES = {
    "panel": ("media_management.panel.app:create_app", 8002),
    "api": ("media_management.api.app:create_app", 8003),
    "public": ("media_management.public.app:create_app", 8001),
}


def serve(process: str, host: str, port: int | None) -> None:
    import uvicorn

    from media_management.logs import setup_logging

    setup_logging(process)
    target, default_port = PROCESSES[process]
    # Sin proxy_headers: la IP del cliente es la del socket, y cada proceso decide por
    # su cuenta de quién se fía (Traefik para el panel, nginx para el público).
    # El público no tiene access log propio: el ticket va en la ruta, y nginx ya
    # registra cada petición con la IP real y el ticket tapado.
    uvicorn.run(target, factory=True, host=host, port=port or default_port,
                proxy_headers=False, server_header=False, log_config=None,
                access_log=process != "public")


def compare_cmd(candidate: Path, current: Path) -> int:
    from media_management.manifest import compare

    diffs = compare(json.loads(candidate.read_text()), json.loads(current.read_text()))
    for d in diffs:
        sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
    sys.stderr.write(f"{len(diffs)} diferencias\n")
    return 1 if diffs else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="media-management")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve", help="arranca uno de los tres procesos web")
    p_serve.add_argument("process", choices=sorted(PROCESSES))
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int)
    sub.add_parser("worker", help="escaneo periódico, manifiesto y miniaturas")
    p_cmp = sub.add_parser("compare", help="compara dos manifiestos clip a clip")
    p_cmp.add_argument("candidate", type=Path)
    p_cmp.add_argument("current", type=Path)
    p_seed = sub.add_parser("demo-seed", help="siembra la biblioteca del modo demo (solo directorios de demo)")
    p_seed.add_argument("--jellyfin-images", type=Path, required=True,
                        help="directorio del que el Jellyfin falso sirve las miniaturas")
    args = parser.parse_args()
    if args.cmd == "serve":
        serve(args.process, args.host, args.port)
    elif args.cmd == "worker":
        from media_management.logs import setup_logging
        from media_management.settings import get_settings
        from media_management.worker import run

        setup_logging("worker")
        asyncio.run(run(get_settings()))
    elif args.cmd == "compare":
        sys.exit(compare_cmd(args.candidate, args.current))
    elif args.cmd == "demo-seed":
        from media_management.demo.seed import NotDemo, seed
        from media_management.logs import setup_logging
        from media_management.settings import get_settings

        setup_logging("demo-seed")
        try:
            done = seed(get_settings(), args.jellyfin_images)
        except NotDemo as e:
            sys.stderr.write(f"{e}\n")
            sys.exit(2)
        if not done:
            sys.stderr.write("el demo ya tiene datos: no se toca nada (./demo.sh reset para volver a sembrar)\n")
