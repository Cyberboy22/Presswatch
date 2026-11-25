"""Compute the experiments behind the README and write docs/ergebnisse.md (+ figures).

Run:  presswatch experiment            (full, about 2 minutes)
      presswatch experiment --quick    (fewer repetitions)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from presswatch.experiments.scenarios import SCENARIOS, run_scenario
from presswatch.features import features_of
from presswatch.models import TARGETS, train_model
from presswatch.sim.doe import doe_cycles
from presswatch.sim.process import SPEC
from presswatch.sim.scenario import ProductionLine

# Colours and marks follow the dashboard: 2px lines, 8px markers, hairline grid.
BLUE, ORANGE, INK, INK_2, GRID, AXIS, CRITICAL = (
    "#257de0",
    "#f47230",
    "#191919",
    "#666666",
    "#e7e7e7",
    "#c2c2c2",
    "#dc3c3d",
)


def _dataset(cycles):
    rows = [features_of(c) for c in cycles]
    y = {t: np.array([getattr(c.truth, t) for c in cycles]) for t in TARGETS}
    return rows, y


def _rmse(model, data, target):
    rows, y = data
    return float(np.sqrt(np.mean((model.predict(rows)[target] - y[target]) ** 2)))


def model_comparison(sizes=(30, 60, 100, 150, 300), repeats=3) -> list[dict]:
    """RMSE of both model types by training size, on the whole window and in production."""
    doe_test = _dataset(doe_cycles(300, seed=999))
    line = ProductionLine(seed=5)
    production = _dataset([line.next_cycle() for _ in range(600)])
    results = []
    for size in sizes:
        for kind in ("hybrid", "baseline"):
            scores = {f"{t}/{where}": [] for t in TARGETS for where in ("doe", "produktion")}
            for r in range(repeats):
                model = train_model(kind, *_dataset(doe_cycles(size, seed=100 + r)), seed=r).model
                for t in TARGETS:
                    scores[f"{t}/doe"].append(_rmse(model, doe_test, t))
                    scores[f"{t}/produktion"].append(_rmse(model, production, t))
            results.append(
                {"n": size, "kind": kind} | {k: float(np.mean(v)) for k, v in scores.items()}
            )
    return results


def scenario_table(seeds=(7, 8, 9, 10, 11), n_cycles=1500) -> list[dict]:
    return [
        run_scenario(name, n_cycles=n_cycles, seed=seed) for name in SCENARIOS for seed in seeds
    ]


def _style(ax):
    ax.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.yaxis.label.set_color(INK_2)
    ax.xaxis.label.set_color(INK_2)


def _plot_comparison(results, path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), dpi=150)
    for ax, target, label, scale, unit in (
        (axes[0], "pv_um", "Formabweichung PV", 1, "µm"),
        (axes[1], "center_thickness_mm", "Mittendicke", 1000, "µm"),
    ):
        for kind, color, name in (
            ("hybrid", BLUE, "Hybridmodell"),
            ("baseline", ORANGE, "Black-Box"),
        ):
            rows = [r for r in results if r["kind"] == kind]
            ax.plot(
                [r["n"] for r in rows],
                [r[f"{target}/doe"] * scale for r in rows],
                color=color,
                linewidth=2,
                marker="o",
                markersize=7,
                markeredgecolor="white",
                markeredgewidth=2,
                label=name,
            )
        ax.set_title(
            f"{label}: Fehler (RMSE) im ganzen Prozessfenster", color=INK, fontsize=10, loc="left"
        )
        ax.set_xlabel("Anzahl Trainingsversuche")
        ax.set_ylabel(unit)
        ax.set_ylim(bottom=0)
        _style(ax)
    axes[0].legend(frameon=False, fontsize=9, labelcolor=INK_2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _plot_wear(result, path: Path) -> None:
    import matplotlib.pyplot as plt

    cycles = [m["cycle"] for m in result.measurements]
    fig, ax = plt.subplots(figsize=(10, 3.8), dpi=150)
    ax.plot(
        cycles,
        [m["predicted"]["pv_um"] for m in result.measurements],
        color=BLUE,
        linewidth=2,
        label="Vorhersage",
    )
    ax.scatter(
        cycles,
        [m["measured"]["pv_um"] for m in result.measurements],
        s=22,
        color=ORANGE,
        edgecolors="white",
        linewidths=1,
        zorder=3,
        label="Messung",
    )
    right = min(cycles[-1], (result.first_scrap_cycle or 900) + 200)
    ax.set_xlim(250, right)
    ax.set_ylim(0, 10)
    ax.axhline(SPEC.pv_max_um, color=INK_2, linewidth=1)
    ax.text(right, SPEC.pv_max_um + 0.15, "Grenze 5 µm ", color=INK_2, fontsize=9, ha="right")
    marks = ax.get_xaxis_transform()  # x in data units, y as a fraction of the axes
    for (x, text, color), y in zip(
        (
            (result.fault_start, "Verschleiß beginnt", INK_2),
            (result.first_alarm_cycle, "Alarm", CRITICAL),
            (result.first_scrap_cycle, "Ausschuss beginnt", INK),
        ),
        (0.96, 0.86, 0.76),
        strict=True,
    ):
        if x:
            ax.axvline(x, color=color, linewidth=1.2)
            ax.text(x + 6, y, text, color=color, fontsize=9, va="top", transform=marks)
    ax.set_xlabel("Zyklus")
    ax.set_ylabel("Formabweichung PV [µm]")
    ax.legend(
        frameon=False,
        fontsize=9,
        labelcolor=INK_2,
        loc="lower left",
        bbox_to_anchor=(0, 1.0),
        ncol=2,
    )
    _style(ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fmt(value, digits=2):
    return "–" if value is None else f"{value:.{digits}f}".replace(".", ",")


def write_report(out: Path, quick: bool = False) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    img = out / "img"
    img.mkdir(exist_ok=True)
    comparison = model_comparison(
        sizes=(30, 60, 150) if quick else (30, 60, 100, 150, 300), repeats=1 if quick else 3
    )
    scenarios = scenario_table(seeds=(7, 8) if quick else (7, 8, 9, 10, 11))

    figures = []
    try:
        _plot_comparison(comparison, img / "modellvergleich.png")
        wear = next(r for r in scenarios if r.name == "werkzeugverschleiss")
        _plot_wear(wear, img / "verschleiss.png")
        figures = ["img/modellvergleich.png", "img/verschleiss.png"]
    except ImportError:
        pass

    lines = [
        "# Ergebnisse der Experimente",
        "",
        f"Erzeugt mit `presswatch experiment{' --quick' if quick else ''}` am "
        f"{datetime.now().strftime('%d.%m.%Y %H:%M')}. Alle Daten stammen aus dem Simulator.",
        "",
        "## 1. Hybridmodell gegen Black-Box",
        "",
        "Trainiert auf Einfahrversuchen (Latin Hypercube), getestet auf 300 neuen Versuchen im "
        "ganzen Prozessfenster und auf 600 Linsen aus dem laufenden Betrieb. Fehler als RMSE, "
        "Mittelwert aus "
        f"{1 if quick else 3} Wiederholungen.",
        "",
        "| Versuche | Modell | PV Fenster [µm] | PV Betrieb [µm] "
        "| Dicke Fenster [µm] | Dicke Betrieb [µm] |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for r in comparison:
        lines.append(
            f"| {r['n']} | {'Hybrid' if r['kind'] == 'hybrid' else 'Black-Box'} "
            f"| {_fmt(r['pv_um/doe'])} | {_fmt(r['pv_um/produktion'])} "
            f"| {_fmt(r['center_thickness_mm/doe'] * 1000, 1)} "
            f"| {_fmt(r['center_thickness_mm/produktion'] * 1000, 1)} |"
        )
    if figures:
        lines += ["", f"![Modellvergleich]({figures[0]})"]

    lines += [
        "",
        "## 2. Überwachung in drei Produktionsläufen",
        "",
        "Je Lauf 1500 Zyklen, jede 5. Linse wird gemessen, Fehler ab Zyklus 400. "
        "Werkzeugverschleiß: +0,01 µm Formabweichung je Zyklus. Sensorfehler: Thermoelement oben "
        "driftet mit 0,1 °C je Zyklus bis +20 °C. „Ausschuss beginnt“ heißt: mindestens 20 % der "
        "letzten 50 Linsen außerhalb der Toleranz.",
        "",
        "| Szenario | Seed | erste Warnung | Ursache | Alarm | Ursache "
        "| Ausschuss ab | Vorwarnzeit |",
        "|---|---:|---:|---|---:|---|---:|---:|",
    ]
    for r in scenarios:
        lead = (
            r.first_scrap_cycle - r.first_alarm_cycle
            if r.first_scrap_cycle and r.first_alarm_cycle
            else None
        )
        lines.append(
            f"| {r.name} | {r.seed} "
            f"| {r.first_warning_cycle or '–'} | {r.first_warning_cause or '–'} "
            f"| {r.first_alarm_cycle or '–'} | {r.first_alarm_cause or '–'} "
            f"| {r.first_scrap_cycle or '–'} | {f'{lead} Zyklen' if lead else '–'} |"
        )
    if len(figures) > 1:
        lines += ["", f"![Verschleiß-Szenario]({figures[1]})"]
    lines.append("")
    path = out / "ergebnisse.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
