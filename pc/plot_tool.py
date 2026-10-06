"""Diagramme aus einer Logger-Sitzung.

Aufruf:  python plot_tool.py <sitzungsordner> [--start S --end S] [--title "Oberfräse"]
  --start/--end  Ausschnitt in Sekunden ab Sitzungsbeginn (sonst automatisch um die größte Last)
Erzeugt im Sitzungsordner: lastkurve.png und (falls Burst vorhanden) burst_<n>.png
"""
import argparse, csv, glob, math, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("session")
ap.add_argument("--start", type=float)
ap.add_argument("--end", type=float)
ap.add_argument("--title", default="Werkzeug")
args = ap.parse_args()
S = args.session


def rows(name):
    p = os.path.join(S, name)
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return list(csv.DictReader(f))


win = [r for r in rows("windows.csv") if int(r["n"]) >= 20]
stat = rows("status.csv")
ref = rows("reference.csv")

# ESP-Zeit auf PC-Zeit abbilden: kleinste Verzögerung der Statuspakete
offset = min(float(r["pc_time"]) - float(r["esp_ms"]) / 1000 for r in stat)
t_w = np.array([float(r["esp_ms"]) / 1000 + offset for r in win])
p_raw = np.array([float(r["mean_iv"]) for r in win])
v_raw = np.sqrt(np.array([float(r["mean_vv"]) for r in win]))
t_r = np.array([float(r["pc_time"]) for r in ref]) if ref else np.array([])
p_ref = np.array([float(r["apower"]) for r in ref]) if ref else np.array([])
u_ref = np.array([float(r["voltage"]) for r in ref]) if ref else np.array([])
t0 = t_w[0]

# Kalibrierung: Sekundenmittel der 50-Hz-Rohleistung gegen die Referenz (Stock-Shelly)
k_p, unit = 1.0, "Rohwert"
if len(t_r):
    def ref_at(t):
        idx = np.searchsorted(t_r, t, side="right") - 1
        return p_ref[idx] if idx >= 0 else np.nan
    sec = np.floor(t_w)
    xs, ys = [], []
    for s in np.unique(sec):
        m = sec == s
        r = ref_at(s + 1.0)
        if not np.isnan(r) and r > 20:
            xs.append(p_raw[m].mean()); ys.append(r)
    if len(xs) >= 5:
        xs, ys = np.array(xs), np.array(ys)
        k_p = float((xs * ys).sum() / (xs * xs).sum())
        if k_p < 0:
            k_p = -k_p; p_raw = -p_raw   # Zange falsch herum: Vorzeichen drehen
        unit = "W"
        print(f"Kalibrierung: 1 Rohwert = {k_p:.4g} W ({len(xs)} Sekunden mit Last)")
p_w = p_raw * k_p

# Ausschnitt
if args.start is not None:
    a, b = t0 + args.start, t0 + (args.end if args.end else args.start + 60)
else:
    i_max = int(np.argmax(p_w))
    a, b = t_w[i_max] - 15, t_w[i_max] + 45
m = (t_w >= a) & (t_w <= b)

fig, ax = plt.subplots(3, 1, figsize=(10, 7.5), sharex=True, sharey=True, dpi=200)
tt, pp = t_w[m] - a, p_w[m].copy()
gap = np.where(np.diff(tt) > 0.2)[0]
tt = np.insert(tt, gap + 1, np.nan); pp = np.insert(pp, gap + 1, np.nan)
ax[0].plot(tt, pp, color="#1f6f8b", lw=0.8)
ax[0].set_title(f"50 Hz: umgeflashter Shelly EM Gen3, ein Wert pro Netzperiode ({m.sum()} Werte)", fontsize=10, loc="left")
if len(t_r):
    mr = (t_r >= a - 2) & (t_r <= b)
    ax[1].step(t_r[mr] - a, p_ref[mr], where="post", color="#1f6f8b", lw=1.4)
    ax[1].plot(t_r[mr] - a, p_ref[mr], "o", ms=2.5, color="#1f6f8b")
    ax[1].set_title(f"1 Hz: Shelly Pro 3EM mit Original-Firmware, gleicher Stromkreis ({mr.sum()} Werte)", fontsize=10, loc="left")
    grid = np.arange(a, b, 8.0)
    idx = np.searchsorted(t_r, grid, side="right") - 1
    g = np.where(idx >= 0, p_ref[np.clip(idx, 0, None)], np.nan)
    ax[2].step(grid - a, g, where="post", color="#1f6f8b", lw=1.4)
    ax[2].plot(grid - a, g, "o", ms=2.5, color="#1f6f8b")
    ax[2].set_title(f"8-Sekunden-Raster wie in ha-nilm ({len(grid)} Werte)", fontsize=10, loc="left")
for x in ax:
    x.grid(alpha=.3); x.set_ylabel(unit); x.set_xlim(0, b - a)
ax[2].set_xlabel("Sekunden")
fig.suptitle(f"{args.title}: dieselbe Messung in drei Abtastraten", fontsize=11)
fig.tight_layout()
out = os.path.join(S, "lastkurve.png")
fig.savefig(out)
print("gespeichert:", out)

# Burst: Rohkurve + Oberwellen
for bf in sorted(glob.glob(os.path.join(S, "burst_*.csv"))):
    br = rows(os.path.basename(bf))
    if len(br) < 100:
        continue
    us = np.array([float(r["us"]) for r in br]) / 1e6
    ii = np.array([float(r["i"]) for r in br])
    vv = np.array([float(r["v"]) for r in br])
    rate = len(us) / (us[-1] - us[0])
    # gleichmäßig neu abtasten über ganze Netzperioden (50 Hz)
    n_per = int((us[-1] - us[0]) * 50)
    tu = np.linspace(us[0], us[0] + n_per / 50, n_per * 64, endpoint=False)
    iu = np.interp(tu, us, ii)
    spec = np.abs(np.fft.rfft(iu * np.hanning(len(iu))))
    f = np.fft.rfftfreq(len(iu), tu[1] - tu[0])
    h = []
    for k in range(1, 16):
        sel = (f > 50 * k - 10) & (f < 50 * k + 10)
        h.append(spec[sel].max() if sel.any() else 0)
    h = np.array(h) / h[0] * 100
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8), dpi=200, gridspec_kw={"width_ratios": [1.6, 1]})
    show = us - us[0] < 0.06
    ax[0].plot((us[show] - us[0]) * 1000, ii[show] / np.abs(ii).max(), color="#1f6f8b", lw=1, label="Strom")
    ax[0].plot((us[show] - us[0]) * 1000, vv[show] / np.abs(vv).max(), color="#999999", lw=1, label="Spannung")
    ax[0].set_xlabel("Millisekunden"); ax[0].set_title(f"Rohkurve, {rate:.0f} Messpaare pro Sekunde", fontsize=10, loc="left")
    ax[0].legend(fontsize=8); ax[0].grid(alpha=.3); ax[0].set_yticks([])
    nyq = rate / 2
    ks = np.arange(1, 16)
    ax[1].bar(ks, h, color=["#1f6f8b" if 50 * k < nyq else "#cccccc" for k in ks])
    ax[1].set_xticks(ks); ax[1].set_xlabel("Oberwelle (Vielfaches von 50 Hz)"); ax[1].set_ylabel("% der Grundwelle")
    ax[1].set_title("Oberwellen im Strom", fontsize=10, loc="left"); ax[1].grid(alpha=.3, axis="y")
    fig.suptitle(f"{args.title}: Stromkurve innerhalb weniger Netzperioden", fontsize=11)
    fig.tight_layout()
    out = bf.replace(".csv", ".png")
    fig.savefig(out)
    print("gespeichert:", out, f"({rate:.0f} Paare/s, Nyquist {nyq:.0f} Hz)")
