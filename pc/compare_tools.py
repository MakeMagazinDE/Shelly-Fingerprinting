"""Vergleicht die Stromkurven mehrerer Werkzeuge aus Bursts einer Sitzung.

Aufruf: python compare_tools.py <sitzung> "Name:3,4" "Name:5,6" ... [--out datei.png] [--title "..."]
"""
import argparse, csv, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("session")
ap.add_argument("tools", nargs="+")
ap.add_argument("--out", default="vergleich_leerlauf.png")
ap.add_argument("--title", default="Drei Werkzeuge im Leerlauf, fast gleiche Leistung")
args = ap.parse_args()

HARM = [3, 5, 7, 9, 11, 13]
COLORS = ["#1f6f8b", "#d1495b", "#edae49", "#00798c"]


def load(n):
    with open(os.path.join(args.session, f"burst_{n}.csv")) as f:
        r = list(csv.DictReader(f))
    return (np.array([float(x["us"]) for x in r]) / 1e6,
            np.array([float(x["i"]) for x in r]), np.array([float(x["v"]) for x in r]))


def analyse(n):
    t, i, v = load(n)
    # Netzfrequenz und Phase aus den Nulldurchgängen der Spannung (steigend)
    zc = [t[k] - v[k] * (t[k + 1] - t[k]) / (v[k + 1] - v[k]) for k in range(len(v) - 1) if v[k] < 0 <= v[k + 1]]
    zc = np.array(zc)
    period = np.median(np.diff(zc))
    n_cyc = len(zc) - 1
    # jede Periode auf 128 Punkte, synchron zur Spannung
    grid = np.linspace(0, 1, 128, endpoint=False)
    cyc_i, cyc_v = [], []
    for k in range(n_cyc):
        tt = zc[k] + grid * (zc[k + 1] - zc[k])
        cyc_i.append(np.interp(tt, t, i)); cyc_v.append(np.interp(tt, t, v))
    ci, cv = np.mean(cyc_i, axis=0), np.mean(cyc_v, axis=0)
    spec = np.abs(np.fft.rfft(ci))
    harm = {h: spec[h] / spec[1] * 100 for h in HARM}
    phase = np.degrees(np.angle(np.fft.rfft(ci)[1]) - np.angle(np.fft.rfft(cv)[1]))
    pf = np.mean(ci * cv) / np.sqrt(np.mean(ci ** 2) * np.mean(cv ** 2))
    rate = len(t) / (t[-1] - t[0])
    return dict(ci=ci / np.abs(ci).max(), cv=cv / np.abs(cv).max(), harm=harm, pf=pf,
                f=1 / period, cycles=n_cyc, rate=rate, irms=np.sqrt(np.mean(ci ** 2)))


tools = []
for spec in args.tools:
    name, nums = spec.split(":")
    res = [analyse(int(x)) for x in nums.split(",")]
    tools.append((name, res))
    for k, r in zip(nums.split(","), res):
        print(f"{name:14s} Burst {k:>2}: {r['cycles']} Perioden, {r['rate']:.0f} Paare/s, f={r['f']:.2f} Hz, "
              f"Irms(roh) {r['irms']:.0f}, PF {r['pf']:.3f}, " + ", ".join(f"H{h} {r['harm'][h]:.1f}%" for h in HARM))

fig, ax = plt.subplots(1, 2, figsize=(11, 4.2), dpi=200, gridspec_kw={"width_ratios": [1.3, 1]})
x = np.linspace(0, 20, 128, endpoint=False)
ax[0].plot(x, tools[0][1][0]["cv"], color="#bbbbbb", lw=1.5, label="Netzspannung")
for (name, res), c in zip(tools, COLORS):
    ax[0].plot(x, res[0]["ci"], color=c, lw=1.6, label=name)
ax[0].set_xlabel("Millisekunden (eine Netzperiode)"); ax[0].set_yticks([]); ax[0].grid(alpha=.3)
ax[0].set_title("Stromkurve, gemittelt über alle Perioden eines Bursts", fontsize=10, loc="left")
ax[0].legend(fontsize=8, loc="lower left")
w = 0.8 / len(tools)
for k, ((name, res), c) in enumerate(zip(tools, COLORS)):
    vals = np.array([[r["harm"][h] for h in HARM] for r in res])
    pos = np.arange(len(HARM)) + (k - (len(tools) - 1) / 2) * w
    ax[1].bar(pos, vals.mean(axis=0), w, color=c, label=name,
              yerr=[vals.mean(axis=0) - vals.min(axis=0), vals.max(axis=0) - vals.mean(axis=0)], capsize=2, ecolor="#444444")
ax[1].set_xticks(range(len(HARM))); ax[1].set_xticklabels([f"{h}." for h in HARM])
ax[1].set_xlabel("Oberwelle (Vielfaches von 50 Hz)"); ax[1].set_ylabel("% der Grundwelle")
ax[1].set_title("Oberwellen im Strom", fontsize=10, loc="left"); ax[1].grid(alpha=.3, axis="y")
fig.suptitle(args.title, fontsize=11)
fig.tight_layout()
out = os.path.join(args.session, args.out)
fig.savefig(out)
print("gespeichert:", out)
