import os, sys
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0,'analysis_scripts/request_level')
os.environ.setdefault("FS_SWE_TBT_MS","75")
from exp22_fluidserve import PAPER_STYLE

OUT="results/aggregate_analysis/exp125_arrivals_hour"
df=pd.read_csv(f"{OUT}/exp125_arrival_stability.csv")
cols=[c for c in df.columns if c.startswith("share_")]
arms=list(dict.fromkeys(df["arm"]))
col={"FluidServe v0.4 control":"#1f77b4",
     "FluidServe v0.4 + arrivals term (N=1)":"#ff7f0e",
     "PolyServe (other session)":"#d62728"}
rows=[]
with plt.rc_context(PAPER_STYLE):
    fig,ax=plt.subplots(1,2,figsize=(9.4,3.6))
    for a in arms:
        M=df[df["arm"]==a][cols].to_numpy(dtype=float)
        xs,ys,ns=[],[],[]
        for k in range(1,21):
            d=[0.5*float(np.abs(M[i]-M[i-k]).sum()) for i in range(k,len(M))
               if not (np.isnan(M[i]).any() or np.isnan(M[i-k]).any())]
            if len(d)<20: continue
            xs.append(k*30.0); ys.append(float(np.mean(d))); ns.append(len(d))
        ax[0].plot(xs,ys,marker="o",ms=2.5,lw=1.0,color=col[a],label=a)
        base=ys[0]
        ax[1].plot(xs,[y/base for y in ys],marker="o",ms=2.5,lw=1.0,color=col[a],label=a)
        for x,y,n in zip(xs,ys,ns):
            rows.append(dict(arm=a,lag_s=x,tv_mean=y,tv_ratio_to_lag1=y/base,n_pairs=n,
                             n_repeats=1,scoring="swe per-token 7s/75ms"))
        print(f"{a}\n  lag(s): "+", ".join(f"{x:.0f}s={y:.3f}" for x,y in zip(xs,ys)))
    for A,t,yl in ((ax[0],"movement of the per-instance arrival share vs lag",
                    "mean total variation distance"),
                   (ax[1],"the same, normalised to the lag-1 value",
                    "TV(lag) / TV(30 s)")):
        A.set_title(t,fontsize=8); A.set_ylabel(yl,fontsize=7)
        A.set_xlabel("lag (seconds between the two windows compared)",fontsize=7)
        A.grid(axis="y",ls=":",lw=0.5,alpha=0.6)
    ax[1].axhline(1.0,color="0.4",lw=0.7,ls="--")
    ax[0].legend(fontsize=6,frameon=False,loc="lower right")
    import textwrap
    cap=("A monotone rising curve that saturates is DRIFT: the fleet's arrival "
         "share keeps going somewhere and never returns. An unstable loop would "
         "instead put a PEAK at half its period and a DIP at the period, because "
         "the distribution would come back to where it was. Neither FluidServe "
         "arm has a dip; both rise monotonically and flatten, and the treatment "
         "sits above the control at every lag rather than only at short ones. "
         "One repeat per arm, so no error bars. PolyServe is another session and "
         "loses half its windows to the 80% attribution floor.")
    fig.suptitle("EXP-125: is the arrivals term oscillating? Movement of the "
                 "per-instance arrival share at 20 lags\n"
                 +"\n".join(textwrap.wrap(cap,130)),fontsize=7)
    fig.tight_layout(rect=[0,0,1,0.80])
    fig.savefig(f"{OUT}/exp125_lag_curve.png",dpi=160); plt.close(fig)
pd.DataFrame(rows).to_csv(f"{OUT}/exp125_lag_curve.csv",index=False)
print(f"\nwrote {OUT}/exp125_lag_curve.png and .csv")
