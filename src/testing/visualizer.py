"""
Visualization helpers for ammeter test results.

Kept as a separate module (rather than baked into AmmeterTestFramework) so that
plotting is optional and the core framework doesn't need matplotlib to function --
only run_test()/run_all_tests() call into this module, and only when
analysis.visualization.enabled is true in config.yaml.
"""

import os
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")  # headless backend: no display needed, just writes image files
import matplotlib.pyplot as plt


def plot_single_result(result: Dict, plot_types: List[str], output_dir: str) -> List[str]:
    """
    Generate the requested plot types for a single ammeter's test result
    (as returned by AmmeterTestFramework.run_test). Returns the list of file
    paths written.
    """
    os.makedirs(output_dir, exist_ok=True)
    samples = result.get("samples", [])
    ammeter_type = result.get("ammeter_type", "unknown")
    run_id_short = result.get("run_id", "run")[:8]
    written = []

    if not samples:
        return written

    if "line" in plot_types:
        path = os.path.join(output_dir, f"{ammeter_type}_{run_id_short}_line.png")
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(range(1, len(samples) + 1), samples, marker="o", color="#2a6f97")
        stats = result.get("statistics", {})
        if stats.get("mean") is not None:
            ax.axhline(stats["mean"], color="#e07a5f", linestyle="--", label=f"mean = {stats['mean']:.4f} A")
            ax.legend()
        ax.set_title(f"{ammeter_type.capitalize()} Ammeter - Samples Over Time")
        ax.set_xlabel("Sample #")
        ax.set_ylabel("Current (A)")
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)

    if "histogram" in plot_types:
        path = os.path.join(output_dir, f"{ammeter_type}_{run_id_short}_histogram.png")
        fig, ax = plt.subplots(figsize=(8, 4))
        bins = min(10, max(3, len(samples) // 2))
        ax.hist(samples, bins=bins, color="#588157", edgecolor="black", alpha=0.85)
        ax.set_title(f"{ammeter_type.capitalize()} Ammeter - Sample Distribution")
        ax.set_xlabel("Current (A)")
        ax.set_ylabel("Frequency")
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)

    return written


def plot_comparison(combined_result: Dict, output_dir: str) -> Optional[str]:
    """
    Generate a bar chart comparing mean current (with std-dev error bars) across
    all ammeter types, from the dict returned by AmmeterTestFramework.run_all_tests.
    Returns the file path written, or None if there's nothing to plot.
    """
    per_ammeter = combined_result.get("comparison", {}).get("per_ammeter", {})
    if not per_ammeter:
        return None

    os.makedirs(output_dir, exist_ok=True)
    run_id_short = combined_result.get("run_id", "comparison")[:8]
    path = os.path.join(output_dir, f"comparison_{run_id_short}_bar.png")

    ammeter_types = list(per_ammeter.keys())
    means = [per_ammeter[a]["mean_current"] or 0 for a in ammeter_types]
    std_devs = [per_ammeter[a]["std_dev"] or 0 for a in ammeter_types]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(ammeter_types, means, yerr=std_devs, capsize=6, color="#457b9d")
    ax.set_title("Mean Current by Ammeter Type (error bars = std dev)")
    ax.set_ylabel("Current (A)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)

    return path