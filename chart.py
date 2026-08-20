"""
chart.py - generira jednostavan grafikon ranga i vrijednosti tima kroz sezonu,
koristeći povijest spremljenu u state["history"] (dodaje se jednom tjedno
u weekly_review.py). Vraća PNG bytes spremne za slanje na Telegram.
"""

import io

import matplotlib
matplotlib.use("Agg")  # bez GUI-a, radi u GitHub Actionsu
import matplotlib.pyplot as plt


def render_rank_value_chart(history):
    """history: lista {"gw": int, "rank": int, "value": float, "points": int}"""
    if not history or len(history) < 2:
        return None

    gws = [h["gw"] for h in history]
    ranks = [h["rank"] for h in history]
    values = [h["value"] for h in history]

    fig, ax1 = plt.subplots(figsize=(8, 4.5))

    color1 = "#37003c"  # FPL ljubičasta
    ax1.set_xlabel("Gameweek")
    ax1.set_ylabel("Ukupni rang", color=color1)
    ax1.plot(gws, ranks, color=color1, marker="o", linewidth=2)
    ax1.tick_params(axis="y", labelcolor=color1)
    ax1.invert_yaxis()  # niži rang = bolje, pa ide gore na grafu
    ax1.set_xticks(gws)

    ax2 = ax1.twinx()
    color2 = "#00ff87"  # FPL zelena
    ax2.set_ylabel("Vrijednost tima (£m)", color="#00805a")
    ax2.plot(gws, values, color="#00805a", marker="s", linewidth=2, linestyle="--")
    ax2.tick_params(axis="y", labelcolor="#00805a")

    plt.title("Tvoj rang i vrijednost tima kroz sezonu")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
