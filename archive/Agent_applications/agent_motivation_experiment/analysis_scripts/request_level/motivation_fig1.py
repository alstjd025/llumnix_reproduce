#!/usr/bin/env python3
"""Motivation figure 1: the fleet produces the same tokens and almost none count.

Three existing answers, on the same four engines, at the same offered rates:

  Llumnix (load balance)  no latency awareness at all -- spread the load
  Llumnix SLO             latency aware, no class structure
  PolyServe               class aware, by assigning engines to classes

FluidServe is deliberately absent. A motivation figure that needs the paper's own
system to make its point is not a motivation figure.

Three panels.

  A  what the engines produced, in output tokens per second
  B  how much of that belonged to a request that finished inside its latency
     rule. A and B share a y axis, because the claim is that the first is nearly
     the same across policies while the second is not, and rescaling either
     would hide exactly that
  C  the same question counted in REQUESTS rather than tokens

C is not a restatement of B. B weights every request by how many tokens it
produced, so a policy that keeps the long requests and refuses the short ones
scores well on it; C counts each request once. The two disagree by more than
their shapes suggest -- at 70 req/s the spread across the three policies is 15.2x
on B and 5.5x on C -- and the reason is Llumnix SLO, which rejects 69.2% of
arrivals there and serves the survivors well. It is the best of the three on
goodput and returns 21.4% of what was sent to it.

C therefore carries BOTH denominators, and the distance between them is the
point rather than a caveat. Solid counts every request that arrived, so a
rejection is a violation. Dotted counts only the requests the policy accepted.
Llumnix SLO's dotted line RISES from 56.8% to 71.3% between 45 and 70 req/s
while its solid line falls from 52.4% to 21.4%: read on the accepted set alone
it looks like a policy improving under load, and what it is doing is refusing
more. The other two never reject, so their lines coincide.

The dotted line has no ceiling worth arguing about, and that is the whole reason
it is never quoted alone. It is computed over the set the policy chose to accept,
so a policy accepting only what it is certain to serve reads 100% on it while
returning nothing; Llumnix SLO's 71.3% at 70 req/s is computed over the 30.0% it
accepted, and pushing the rate further would raise the number and shrink the set
again. There is no bound to establish here -- the unbounded behaviour IS the
argument for reporting the solid line beside it.

Rejecting cannot raise the SOLID number, because a rejection is a violation
there. Llumnix SLO's offered attainment falls with load like everyone else's, 52.4
to 21.4 between 45 and 70 req/s. What rises is the dotted number and the distance
to the policies that never reject, 1.6x at 45 req/s and 5.5x at 70. Splitting
every arrival three ways at 70 req/s says why that distance opens:

                        met its rule   served, too late   refused
  Llumnix (load bal.)            3.9               96.1       0.0
  PolyServe                     14.4               85.6       0.0
  Llumnix SLO                   21.4                8.6      70.0

A refusal and a late answer both score zero on the offered denominator, so
Llumnix SLO converted 70 of the 96 requests load balancing served late into
refusals and got 17.5 more requests met in exchange. Those 17.5 are work that
succeeded on capacity the refusals freed, not a smaller denominator.

So what separates the three is not whether they reject. It is how much of the
fleet's work lands in neither column: 96.1%, 85.6%, 8.6%. FluidServe rejects too
-- 45.1% at this rate, with 51.9% met and 3.0% late -- so the claim to make is
that it rejects less than Llumnix SLO and meets more, never that rejecting is
itself the fault.

That three-way split is printed by this script but is no longer drawn: a fourth
panel restating one rate crowded the figure, and the split reads better as a
table. ms_dev/notes/motivation.md section 2.3 carries it.

The split drops requests still running when the run ended, because their outcome
is unknown rather than bad, which is the same denominator attain() uses. Without
that exclusion load balance reads 30.9% met against the 3.9% its attainment
reports, and that mismatch is how the omission was caught.

Below about 35 req/s the three policies are indistinguishable on every panel:
the routing decision does not matter until the fleet is loaded enough for it to.

Data: EXP-53, two repeats per cell, stock vLLM FIFO engine, migration off for
PolyServe and on for the two Llumnix arms. The Llumnix SLO arm uses the `m1f`
workload configuration, which splits swe's 30-second end-to-end budget into the
(first-token, per-token) pair that policy requires; the alternative left it
rejecting 98% of that class and answering a different question.

WARNING -- the PolyServe line is not yet publishable. Its
`--polyserve-tier-decode-tokens` is stale in exactly the way FluidServe's own
length profile was before EXP-48 fixed it: deep research is configured at 275
expected output tokens against a measured 985, a factor of 3.58. So the figure
currently compares a system whose length profile was corrected against a
baseline whose was not, which is an asymmetry in our favour. See
ms_dev/notes/polyserve-fidelity.md section 2 and paper-outline.md section 0. The
fix is one string and the re-run is about three hours.

  python3 motivation_fig1.py <out-dir>
"""
import glob
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run, attain  # noqa: E402

ARMS = [("loadbalance", "Llumnix (load balance)", "#9467bd"),
        ("slo", "Llumnix SLO", "#2ca02c"),
        ("polyserve", "PolyServe", "#d62728")]


def _supersede(df):
    """Drop the EXP-53 PolyServe rows once EXP-57 has re-measured that arm.

    Averaging the two would mix a stale tier length table with a corrected one.
    Prints what it dropped, because a silent supersede is how a figure ends up
    describing runs nobody chose.
    """
    if df.empty or "src" not in df:
        return df
    have_new = ((df["arm"] == "polyserve") & (df["src"] == "exp57")).any()
    if not have_new:
        print("  PolyServe: EXP-53 runs only (stale tier table -- see EXP-57)")
        return df
    old = (df["arm"] == "polyserve") & (df["src"] == "exp53")
    print(f"  PolyServe: using {int((~old & (df.arm=='polyserve')).sum())} EXP-57 "
          f"conditions, superseding {int(old.sum())} from EXP-53")
    return df[~old]


# PolyServe's tier length table was corrected on 2026-08-05 and its arm
# re-measured as EXP-57; its EXP-53 runs used the stale table, which understated
# deep research by 3.58x and fed the repartitioner, so they are SUPERSEDED rather
# than averaged in. The other arms never read that flag and stay on EXP-53.
#
# Joining the two sessions was checked rather than assumed: Llumnix SLO, whose
# code and configuration did not change, was re-run in the EXP-57 session at 45
# and 70 req/s and read 52.3 and 21.7 against EXP-53's 52.4 and 21.4, inside
# EXP-53's own repeat spread. Those two conditions are excluded from the sweep
# below so the repeat count stays even across rates.
def collect(pattern="results/*exp5[37]*_rpm_*"):
    rows = []
    for d in sorted(glob.glob(pattern)):
        base = os.path.basename(d)
        if "unchanged" in base:
            continue
        m = re.search(r"_(polyserve|slo|loadbalance)_m1f?_rpm_(\d+)$", base)
        if not m:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        rej = r["rejected"] if "rejected" in r else pd.Series(False, index=r.index)
        served = r[~rej]
        dur = r["rel"].max()
        met = served[~served["violate_served"]]
        # Panel D splits arrivals three ways, on the SAME denominator attain()
        # uses: requests still running when the run ended are dropped from both,
        # because their outcome is unknown rather than bad. Computing the split
        # without that exclusion put load balance at 30.9% met against the 3.9%
        # its attainment reads, which is how the mismatch was caught.
        k = r[~r["cutoff"]]
        nk = max(len(k), 1)
        k_rej = k["rejected"]
        k_err = k["errored"] & ~k_rej
        k_srv = k[~k_rej & ~k["errored"]]
        k_met = k_srv[~k_srv["violate_served"]]
        rows.append(dict(arm=m.group(1), rate=int(m.group(2)) / 60.0,
                         src="exp57" if "exp57" in base else "exp53",
                         thru=served["output_tokens"].sum() / dur,
                         good=met["output_tokens"].sum() / dur,
                         att_off=attain(r, "violate_offered"),
                         att_adm=attain(served, "violate_served"),
                         rej=100.0 * rej.mean(),
                         o_met=100.0 * len(k_met) / nk,
                         o_late=100.0 * (len(k_srv) - len(k_met)) / nk,
                         o_rej=100.0 * k_rej.sum() / nk,
                         o_err=100.0 * k_err.sum() / nk))
    return _supersede(pd.DataFrame(rows))


def main(out):
    df = collect()
    if df.empty:
        sys.exit("no EXP-53 runs matched")
    g = df.groupby(["arm", "rate"])
    agg = g.agg(thru=("thru", "mean"), good=("good", "mean"),
                thru_lo=("thru", "min"), thru_hi=("thru", "max"),
                good_lo=("good", "min"), good_hi=("good", "max"),
                att_off=("att_off", "mean"), att_adm=("att_adm", "mean"),
                att_off_lo=("att_off", "min"), att_off_hi=("att_off", "max"),
                rej=("rej", "mean"), o_met=("o_met", "mean"),
                o_late=("o_late", "mean"), o_rej=("o_rej", "mean"),
                o_err=("o_err", "mean"),
                n=("thru", "size")).reset_index()

    for q, lab in (("thru", "total produced (output tokens/s)"),
                   ("good", "met its rule -- goodput (output tokens/s)"),
                   ("att_off", "SLO attainment (%), offered denominator"),
                   ("att_adm", "SLO attainment (%), admitted denominator"),
                   ("rej", "rejected (%)")):
        p = agg.pivot(index="rate", columns="arm", values=q)
        cols = [k for k, _, _ in ARMS if k in p.columns]
        print(f"\n  {lab}")
        print(p[cols].to_string(float_format=lambda v: f"{v:,.1f}"))
        if q != "rej":
            print("  max/min across the three: " +
                  " ".join(f"{r:.0f}:{v:.1f}x" for r, v in
                           (p[cols].max(axis=1)
                            / p[cols].min(axis=1).replace(0, np.nan)).items()))
    # No panel draws this any more, so the script has to print it or the
    # numbers quoted in motivation.md section 2.3 stop being reproducible.
    print("\n  what became of every request that arrived (%), on the same "
          "denominator\n  as attainment -- requests still running at the end of "
          "the run are excluded")
    print(f"    {'rate':>5}  " + "  ".join(f"{l:>26}" for _, l, _ in ARMS))
    print(f"    {'':>5}  " + "  ".join(f"{'met':>8}{'late':>9}{'refused':>9}"
                                       for _ in ARMS))
    for rate in sorted(agg["rate"].unique()):
        cells = []
        for k, _, _ in ARMS:
            g_ = agg[(agg.arm == k) & (agg.rate == rate)]
            if g_.empty:
                cells.append(" " * 26)
                continue
            cells.append(f"{g_['o_met'].iloc[0]:>8.1f}{g_['o_late'].iloc[0]:>9.1f}"
                         f"{g_['o_rej'].iloc[0]:>9.1f}")
        print(f"    {rate:>5.0f}  " + "  ".join(cells))

    print("\nrepeats per cell: " + str(sorted(agg["n"].unique())))

    HL_CAP = 70.0   # the rate the caption quotes

    os.makedirs(out, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(10.2, 3.0))
        # Only the first two share an axis. They are the pair that carries the
        # claim -- same units, one nearly flat across policies and one not --
        # and rescaling either would hide exactly that.
        ax[1].sharey(ax[0])
        for key, lab, col in ARMS:
            s = agg[agg.arm == key].sort_values("rate")
            if s.empty:
                continue
            for i, (m_, lo, hi) in enumerate((("thru", "thru_lo", "thru_hi"),
                                              ("good", "good_lo", "good_hi"))):
                ax[i].plot(s.rate, s[m_], color=col, marker="o", ms=3.5,
                           mec="white", mew=0.5, label=lab)
                ax[i].fill_between(s.rate, s[lo], s[hi], color=col, alpha=0.18,
                                   lw=0)
            # Panel C counts requests rather than tokens, and carries BOTH
            # denominators. Solid counts every request that arrived, so a
            # rejection is a violation; dotted counts only what the policy
            # accepted. The distance between them is what that policy bought by
            # refusing work, and reporting either one alone is what makes a
            # policy that rejects most of its load look either broken or
            # excellent depending on which was picked.
            ax[2].plot(s.rate, s.att_off, color=col, marker="o", ms=3.5,
                       mec="white", mew=0.5, ls="-")
            ax[2].plot(s.rate, s.att_adm, color=col, ls=":", lw=1.1)
            ax[2].fill_between(s.rate, s.att_off, s.att_adm, color=col,
                               alpha=0.10, lw=0)

        # Where the policies start to differ. Read off the goodput panel, where
        # the three are within 1% up to 25 req/s and 2.1x apart by 35.
        for a_ in ax[:3]:
            a_.axvspan(15, 35, color="#888888", alpha=0.06, lw=0)
        ax[0].annotate("all three the same\n(the decision does not\nmatter yet)",
                       (25, 3200), fontsize=6.5, color="#666666", ha="center")
        ax[1].annotate("all three the same", (25, 3200), fontsize=6.5,
                       color="#666666", ha="center")

        ax[1].annotate("20,057 produced\n→ 710 useful",
                       (70, 710), xytext=(47, 5600), fontsize=7, color="#9467bd",
                       arrowprops=dict(arrowstyle="->", color="#9467bd", lw=0.8))

        # The gap on the arm that creates it. Llumnix SLO rejects 69.2% at 70
        # req/s, which is the whole distance between its two lines.
        s_ = agg[(agg.arm == "slo") & (agg.rate == 70.0)]
        if not s_.empty:
            off = float(s_["att_off"].iloc[0])
            adm = float(s_["att_adm"].iloc[0])
            rej = float(s_["o_rej"].iloc[0])
            ax[2].annotate("", xy=(70, adm), xytext=(70, off),
                           arrowprops=dict(arrowstyle="<->", color="#2ca02c",
                                           lw=0.9))
            # Under the flat stretch, which is the only empty region: every
            # line sits at 100 up to 35 req/s, so nothing is covered there.
            ax[2].annotate(f"{adm:.0f}% — but of the {100 - rej:.0f}%\nit accepted",
                           (70, adm), xytext=(56, 86), fontsize=6.5,
                           color="#2ca02c", ha="center",
                           arrowprops=dict(arrowstyle="->", color="#2ca02c",
                                           lw=0.7))
            ax[2].annotate(f"refuses {rej:.0f}%", (70, off), xytext=(46, 8),
                           fontsize=6.5, color="#2ca02c", ha="center",
                           arrowprops=dict(arrowstyle="->", color="#2ca02c",
                                           lw=0.7))

        ax[0].set_title("A. what the engines produced", fontsize=8)
        ax[1].set_title("B. how much of it met its latency rule", fontsize=8)
        ax[2].set_title("C. and how many REQUESTS were met", fontsize=8)
        ax[0].set_ylabel("output tokens per second")
        ax[2].set_ylabel("SLO attainment (%), per request")
        for a_ in ax[:3]:
            a_.set_xlabel("offered request rate (req/s)")
            a_.set_xlim(10, 75)
            a_.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        for a_ in ax[:2]:
            a_.set_ylim(0, 22000)
        ax[2].set_ylim(0, 105)

        # Two legends. Colour is the policy and line style is the denominator,
        # and a single combined legend reads as though the styles were more
        # policies. Built from what was drawn rather than from a fixed list.
        h, l = ax[0].get_legend_handles_labels()
        ax[0].legend(h, l, loc="upper center", bbox_to_anchor=(1.1, -0.22),
                     ncol=3, fontsize=7, columnspacing=1.4)
        den = [plt.Line2D([], [], color="#444444", ls="-", lw=1.2),
               plt.Line2D([], [], color="#444444", ls=":", lw=1.2)]
        ax[2].legend(den,
                     ["every request that arrived\n(a rejection is a violation)",
                      "only the requests it accepted"],
                     loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=2,
                     fontsize=6.3, columnspacing=1.0, handlelength=2.0)

        # Every number in the caption is read from the data. Hard-coding them
        # went stale the first time an arm was re-measured: the caption still
        # said the best policy returned 21% after EXP-57 moved that to 24% and
        # changed which policy it was.
        hi = agg[agg.rate == HL_CAP]
        thr_r = hi["thru"].max() / hi["thru"].min()
        good_r = hi["good"].max() / hi["good"].min()
        best = hi.loc[hi["att_off"].idxmax()]
        best_name = next(l for k, l, _ in ARMS if k == best["arm"])
        slo = agg[(agg.arm == "slo") & (agg.rate == HL_CAP)]
        fig.suptitle(
            f"Three existing policies, the same four engines, the same load. At "
            f"{HL_CAP:.0f} req/s they produce within {thr_r:.1f}x of each other "
            f"and differ by {good_r:.1f}x in how much of that production a "
            f"client could use,\nand the best of them ({best_name}) returns "
            f"{best['att_off']:.0f}% of the requests sent to it inside the "
            f"latency it promised. In C the solid lines count every request "
            f"that arrived and the dotted\nones only the accepted set: Llumnix "
            f"SLO's dotted line rises to {float(slo['att_adm'].iloc[0]):.0f}% "
            f"because the set it covers shrinks to "
            f"{100 - float(slo['o_rej'].iloc[0]):.0f}%, and a policy refusing "
            f"everything would read 100% there,\nwhich is why the dotted number "
            f"is never quoted on its own. Shaded band in A and B is the spread "
            f"over repeats.",
            fontsize=8, y=1.16)
        p = os.path.join(out, "motivation_throughput_vs_goodput.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "results/aggregate_analysis/motivation")
