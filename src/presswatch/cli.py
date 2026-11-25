"""Command line: presswatch demo | api | cell | collector | experiment."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from presswatch.config import Settings


def _logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("asyncua").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _demo(args) -> None:
    import uvicorn

    from presswatch.api.app import create_app

    db_path = Path(args.db)
    if args.fresh and db_path.exists():
        db_path.unlink()
    endpoint = f"opc.tcp://127.0.0.1:{args.opcua_port}/presswatch/"
    settings = Settings.from_env(
        database_url=f"sqlite:///{db_path}",
        embedded_sim=True,
        api_url=f"http://127.0.0.1:{args.port}",
        opcua_endpoint=endpoint,
        opcua_listen=endpoint,
        cycle_period_s=args.period,
        sample_every=2,
        measurement_delay_cycles=2,
    )
    print(
        "\n  PressWatch-Demo\n"
        f"  Dashboard:       http://127.0.0.1:{args.port}/\n"
        f"  Werker-Ansicht:  http://127.0.0.1:{args.port}/werker\n"
        f"  API-Doku:        http://127.0.0.1:{args.port}/docs\n"
        f"  OPC UA:          {endpoint}\n"
        f"  Datenbank:       {db_path.resolve()}\n"
        "  Beenden mit Strg+C.\n",
        flush=True,
    )
    uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, log_level="warning")


def _api(args) -> None:
    import uvicorn

    from presswatch.api.app import create_app

    uvicorn.run(create_app(Settings.from_env()), host=args.host, port=args.port, log_level="info")


def _cell(_args) -> None:
    from presswatch.opcua.cell import ProductionCell

    asyncio.run(ProductionCell(Settings.from_env()).run())


def _collector(_args) -> None:
    from presswatch.opcua.collector import Collector

    asyncio.run(Collector(Settings.from_env()).run())


def _experiment(args) -> None:
    from presswatch.experiments.report import write_report

    path = write_report(Path(args.out), quick=args.quick)
    print(f"Bericht geschrieben: {path}")


def _record(args) -> None:
    from presswatch.static_site import build_site

    out = build_site(Path(args.out), frame_every=args.frame_every)
    print(
        f"Statische Seite geschrieben: {out.resolve()}\n"
        "Auf https://app.netlify.com/drop ziehen (Ordner) – oder lokal ansehen mit:\n"
        f"  python -m http.server 8090 --directory {out}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="presswatch",
        description="Qualitätsvorhersage und Drift-Überwachung für nicht-isothermes Glaspressen",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Logs")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="alles in einem Prozess: Zelle, Collector, API, Dashboard")
    demo.add_argument("--port", type=int, default=8000)
    demo.add_argument("--opcua-port", type=int, default=4840)
    demo.add_argument("--db", default="presswatch-demo.db", help="SQLite-Datei")
    demo.add_argument("--period", type=float, default=0.5, help="Sekunden pro Presszyklus")
    demo.add_argument("--fresh", action="store_true", help="mit leerer Datenbank starten")
    demo.set_defaults(func=_demo)

    api = sub.add_parser("api", help="nur die REST-API mit Dashboard")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", type=int, default=8000)
    api.set_defaults(func=_api)

    sub.add_parser(
        "cell", help="simulierte Fertigungszelle (OPC-UA-Server + Messstation)"
    ).set_defaults(func=_cell)
    sub.add_parser("collector", help="Collector: OPC UA → REST-API").set_defaults(func=_collector)

    experiment = sub.add_parser("experiment", help="Experimente rechnen und Bericht schreiben")
    experiment.add_argument("--out", default="docs", help="Zielordner")
    experiment.add_argument("--quick", action="store_true", help="weniger Wiederholungen")
    experiment.set_defaults(func=_experiment)

    record = sub.add_parser("record", help="Lauf aufzeichnen und statische Seite für Netlify bauen")
    record.add_argument("--out", default="site", help="Zielordner")
    record.add_argument("--frame-every", type=int, default=5, help="Bild alle n Zyklen")
    record.set_defaults(func=_record)

    args = parser.parse_args(argv)
    _logging(args.verbose)
    try:
        args.func(args)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
