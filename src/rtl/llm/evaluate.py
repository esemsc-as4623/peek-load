"""Score every Claude labelling config against the human gold labels.

    pixi run label export && pixi run label-eval

Truth = the labeller's first pass in data/gold/gold_labels.csv; the second pass on 50 repeats gives the human's own
consistency, the ceiling no model can be fairly expected to beat. Per config: accuracy, macro-F1 (over classes
present in gold), abstain rate, calibration error, live $ per 1,000 buildings, with breakdowns by sample group and
tagged/untagged. Recommendation: the cheapest config whose macro-F1 is within TOLERANCE of the best.

Outputs: reports/labels/eval.md, reports/labels/recommendation.json, reports/figures/labels_confusion.png
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from rtl.llm.cache import Cache
from rtl.llm.label import OUT as LABELS
from rtl.schemas import LABEL_CLASSES
from rtl.settings import GOLD_DIR, REPORTS_DIR

TOLERANCE = 0.03
OUT_DIR = REPORTS_DIR / "labels"
# cue families an answer can cite; counted in the `evidence` text to measure use of context
CUES = {"size": r"m²|larger|smaller|size|median neighbour|x the", "floors": r"floor|storey|height",
        "road": r"road|fronts|frontage", "landuse": r"land-use|land use|landuse|zone",
        "density": r"density|cluster|within 50 m|neighbour", "tag": r"tag|osm|overture|registry|amenity|shop=",
        "market": r"market|trading", "shape": r"elongat|compact|rectang|x \d+ m", "wealth": r"wealth|rwi"}


def diversity(claude: pd.DataFrame) -> pd.DataFrame:
    """Per labeler, over every building it labelled: class mix, normalised entropy, non-residential share and the
    mean number of distinct cue families cited in its evidence."""
    out = []
    for lab, g in claude.groupby("labeler"):
        share = g.label.value_counts(normalize=True)
        ent = float(-(share * np.log(share)).sum() / np.log(len(LABEL_CLASSES)))
        ev = g.probs_json.fillna("{}").map(lambda j: json.loads(j).get("_evidence", "")).str.lower()
        cues = sum(ev.str.contains(pat, regex=True).astype(int) for pat in CUES.values())
        non_res = float(1 - share.get("residential", 0) - share.get("unknown", 0))
        out.append({"labeler": lab, "n": len(g), "entropy": ent, "non_residential": non_res,
                    "unknown": float(share.get("unknown", 0)), "cues_cited": cues.mean(),
                    **{f"%{c}": share.get(c, 0.0) for c in LABEL_CLASSES}})
    return pd.DataFrame(out).sort_values("labeler")


def macro_f1(y: pd.Series, p: pd.Series) -> float:
    f1s = []
    for c in sorted(set(y)):
        tp = ((p == c) & (y == c)).sum()
        prec = tp / max(1, (p == c).sum())
        rec = tp / max(1, (y == c).sum())
        f1s.append(0.0 if tp == 0 else 2 * prec * rec / (prec + rec))
    return float(np.mean(f1s)) if f1s else float("nan")


def ece(conf: pd.Series, correct: pd.Series, bins: int = 10) -> float:
    """Expected calibration error: |accuracy - confidence| averaged over confidence bins, weighted by count."""
    d = pd.DataFrame({"c": conf, "ok": correct.astype(float)}).dropna()
    if d.empty:
        return float("nan")
    d["b"] = np.minimum((d.c * bins).astype(int), bins - 1)
    g = d.groupby("b").agg(n=("ok", "size"), acc=("ok", "mean"), conf=("c", "mean"))
    return float((g.n * (g.acc - g.conf).abs()).sum() / g.n.sum())


_LEDGER: pd.DataFrame | None = None


def cost_per_1k(labeler: str) -> float:
    global _LEDGER
    if _LEDGER is None:  # read once; the cache lock is shared with any running labelling job
        _LEDGER = Cache().ledger()
    model, variant, pv = labeler.split("|")
    led = _LEDGER[_LEDGER.prompt_version == pv] if (_LEDGER.prompt_version == pv).any() else _LEDGER
    rows = led[(led.model == model) & led.tag.fillna("").str.endswith(variant)]
    if rows.empty:
        return float("nan")
    live = rows[~rows.batch]
    return float((live if len(live) else rows).cost_usd.mean() * 1000)


def main() -> None:
    gold_all = pd.read_csv(GOLD_DIR / "gold_labels.csv")
    first = gold_all[~gold_all.repeat].drop_duplicates("bldg_id", keep="last").set_index("bldg_id")
    second = gold_all[gold_all.repeat].drop_duplicates("bldg_id", keep="last").set_index("bldg_id")
    rep = first.join(second, rsuffix="_2", how="inner")
    human_agree = float((rep.label == rep.label_2).mean()) if len(rep) else float("nan")

    meta = pd.read_csv(GOLD_DIR / "sample_ids.csv").set_index("bldg_id")
    claude = pd.read_parquet(LABELS)
    gold_ids = set(meta[meta.in_gold].index)
    div = diversity(claude[claude.bldg_id.isin(gold_ids)])  # same 400 buildings for every labeler
    m = claude.merge(first[["label"]].rename(columns={"label": "gold"}), left_on="bldg_id", right_index=True)
    m = m.join(meta[["group", "tagged"]], on="bldg_id")
    m["correct"] = m.label == m.gold

    rows, by_group = [], []
    for lab, g in m.groupby("labeler"):
        rows.append({"labeler": lab, "n": len(g), "accuracy": g.correct.mean(), "macro_f1": macro_f1(g.gold, g.label),
                     "abstain": g.abstain.mean(), "ece": ece(g.confidence, g.correct), "usd_per_1k": cost_per_1k(lab)})
        for (grp, tag), gg in g.groupby(["group", "tagged"]):
            by_group.append({"labeler": lab, "group": grp, "tagged": tag, "n": len(gg),
                             "accuracy": gg.correct.mean(), "macro_f1": macro_f1(gg.gold, gg.label)})
    res = pd.DataFrame(rows).sort_values("macro_f1", ascending=False)
    best = res.macro_f1.max()
    ok = res[res.macro_f1 >= best - TOLERANCE].sort_values("usd_per_1k")
    rec = ok.iloc[0].to_dict() if len(ok) else {}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "recommendation.json").write_text(json.dumps(
        {"recommended": rec.get("labeler"), "macro_f1": rec.get("macro_f1"), "usd_per_1k": rec.get("usd_per_1k"),
         "best_macro_f1": best, "tolerance": TOLERANCE, "n_gold": len(first),
         "human_self_agreement": human_agree, "n_repeats": len(rep)}, indent=2, default=float) + "\n")
    gold_dist = first.label.value_counts().reindex(LABEL_CLASSES, fill_value=0)
    md = ["# Building-use labelling: Claude vs human gold", "",
          f"- gold buildings labelled: {len(first)} (first pass); repeats: {len(rep)}; "
          f"human self-agreement: {human_agree:.1%}",
          f"- recommended config (cheapest within {TOLERANCE} macro-F1 of best): **{rec.get('labeler')}**", "",
          "## Per config", "", res.round(3).to_string(index=False), "",
          "## Diversity and use of context (all 400 gold candidates, gold labels not needed)", "",
          div.round(3).to_string(index=False), "",
          "## By group and tagged", "", pd.DataFrame(by_group).round(3).to_string(index=False), "",
          "## Gold class distribution", "", gold_dist.to_string()]
    (OUT_DIR / "eval.md").write_text("\n".join(md) + "\n")
    print("\n".join(md[:6]))
    print(res.round(3).to_string(index=False))
    print(div[["labeler", "entropy", "non_residential", "unknown", "cues_cited"]].round(3).to_string(index=False))

    if rec:
        plot_confusion(m[m.labeler == rec["labeler"]], rec["labeler"])


def plot_confusion(g: pd.DataFrame, labeler: str) -> None:
    import matplotlib.pyplot as plt

    from rtl import viz

    viz.use()
    cls = [c for c in LABEL_CLASSES if c in set(g.gold) | set(g.label)]
    cm = pd.crosstab(g.gold, g.label).reindex(index=cls, columns=cls, fill_value=0)
    share = cm.div(cm.sum(axis=1).replace(0, 1), axis=0)
    fig, ax = plt.subplots(figsize=(8, 7))
    cmap = viz.SEQ_BLUE.copy()
    cmap.set_bad(viz.SURFACE)  # empty cells stay blank instead of looking like small counts
    im = ax.imshow(np.ma.masked_where(cm.to_numpy() == 0, share.to_numpy()), cmap=cmap, vmin=0, vmax=1)
    cb = fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02, ticks=[0, 0.5, 1])
    cb.ax.set_yticklabels(["0%", "50%", "100%"])
    cb.set_label("share of the human label's row", color=viz.INK_2)
    cb.outline.set_visible(False)
    for i in range(len(cls)):
        for j in range(len(cls)):
            if cm.iat[i, j]:
                ax.text(j, i, cm.iat[i, j], ha="center", va="center", fontsize=8,
                        color="white" if share.iat[i, j] > 0.55 else viz.INK)
    ax.set_xticks(range(len(cls)), [c.replace("_", " ") for c in cls], rotation=40, ha="right")
    ax.set_yticks(range(len(cls)), [c.replace("_", " ") for c in cls])
    ax.set_xlabel("Claude label")
    ax.set_ylabel("human gold label")
    ax.grid(False)
    acc = float((g.gold == g.label).mean())
    ax.set_title(f"Claude vs human gold labels ({labeler.split('|')[0]}, card {labeler.split('|')[2]}): "
                 f"n={len(g)}, agreement {acc:.0%}", fontsize=10)
    fig.subplots_adjust(bottom=0.27)
    fig.text(0.01, 0.01, "Cell colour = share of the human label's row (0–100%); numbers = buildings. Human labels "
             "mostly used imagery; Claude saw only open vector data.", fontsize=8, color=viz.INK_2)
    fig.savefig(REPORTS_DIR / "figures" / "labels_confusion.png", bbox_inches=None)


if __name__ == "__main__":
    main()
