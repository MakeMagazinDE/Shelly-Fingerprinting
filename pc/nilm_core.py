"""Kern der Werkzeugerkennung: Einschalt-Erkennung, Fingerabdruck, Bibliothek mit nächstem Nachbarn."""
import json, math, os
import numpy as np

K_WATT = 1.40e-9          # Rohwert -> Watt, kalibriert gegen den Pro 3EM
STEP_ON = 40.0            # W über Grundlast = etwas wurde eingeschaltet
STEP_OFF = 25.0           # W über Grundlast = wieder aus (geglättet)
BURST_AT = 2.5            # s nach dem Einschalten: Burst für die Stromkurve
HARM = (3, 5, 7, 9)

# Feature-Namen, Anzeige, Einheit, angenommene Streuung bei nur einem Beispiel
FEATURES = [
    ("log_peak",   "Anlaufspitze",            "W",  0.10),
    ("log_step",   "Leistung nach Anlauf",    "W",  0.06),
    ("settle",     "Anlaufdauer",             "s",  0.15),
    ("phase",      "Phasenwinkel Strom",      "°",  1.0),
    ("h3",         "3. Oberwelle",            "%",  0.7),
    ("h5",         "5. Oberwelle",            "%",  0.5),
    ("h7",         "7. Oberwelle",            "%",  0.5),
    ("h9",         "9. Oberwelle",            "%",  0.5),
]
FNAMES = [f[0] for f in FEATURES]


class Detector:
    """Bekommt 50-Hz-Werte (t, Watt) und meldet Einschaltvorgänge mit Zeitmerkmalen."""

    def __init__(self):
        self.t, self.p = [], []
        self.base = None
        self.state = "idle"
        self.t_on = None
        self.below_since = None

    def feed(self, t, p):
        self.t.append(t); self.p.append(p)
        if len(self.t) > 3000:
            self.t, self.p = self.t[-2000:], self.p[-2000:]
        events = []

        def window(sec):
            return np.array([pp for tt, pp in zip(self.t[-1100:], self.p[-1100:]) if t - tt < sec])

        def med(n):
            return float(np.median(self.p[-n:]))

        def spread(w):          # robuste Schwankungsbreite, unempfindlich gegen einzelne Ausreißer (LED-Flackern)
            return float(np.percentile(w, 90) - np.percentile(w, 10))

        if self.state == "idle":
            if self.base is not None and len(self.p) >= 5 and med(5) > self.base + STEP_ON:
                k = next(i for i in range(-8, 0) if self.p[i] > self.base + STEP_ON)
                self.state, self.t_on, self.below_since = "on", self.t[k], None
                events.append(("on", self.t_on, self.base))
            else:
                w = window(1.5)
                if len(w) >= 40 and spread(w) < 30:      # ruhiges Signal: Grundlast nachführen
                    self.base = float(np.median(w))
        else:
            if len(self.p) >= 9 and med(9) < self.base + STEP_OFF:
                self.below_since = self.below_since or t
                if t - self.below_since > 1.0:
                    self.state, self.below_since = "idle", None
                    events.append(("off", t, self.base))
            else:
                self.below_since = None
                w = window(20)
                if t - self.t_on > 20 and len(w) > 600 and spread(w) < 40:
                    # bleibt dauerhaft an (z. B. Licht): neue Grundlast
                    self.base = float(np.median(window(2)))
                    self.state = "idle"
                    events.append(("steady", t, self.base))
        return events

    def time_features(self, t_on, base):
        t = np.array(self.t); p = np.array(self.p) - base
        m_peak = (t >= t_on) & (t <= t_on + 1.0)
        m_step = (t >= t_on + 1.5) & (t <= t_on + BURST_AT - 0.1)
        if m_peak.sum() < 10 or m_step.sum() < 10:
            return None
        peak = float(p[m_peak].max())
        step = float(np.median(p[m_step]))
        if step < STEP_ON / 2:
            return None
        thr = step + 0.25 * max(peak - step, 0)
        tt, pp = t[m_peak | m_step], p[m_peak | m_step]
        i_peak = int(np.argmax(pp[: m_peak.sum()]))
        settle = next((tt[k] - t_on for k in range(i_peak, len(pp)) if pp[k] <= thr), tt[-1] - t_on)
        return {"peak_w": peak, "step_w": step, "log_peak": math.log10(max(peak, 1)),
                "log_step": math.log10(max(step, 1)), "peak_ratio": peak / step, "settle": float(settle)}


def mean_cycle(us, i, v, n=128):
    """Mittelt alle Netzperioden eines Bursts, synchron zum steigenden Nulldurchgang der Spannung."""
    t = np.asarray(us, float) / 1e6; i = np.asarray(i, float); v = np.asarray(v, float)
    zc = np.array([t[j] - v[j] * (t[j + 1] - t[j]) / (v[j + 1] - v[j]) for j in range(len(v) - 1) if v[j] < 0 <= v[j + 1]])
    if len(zc) < 3:
        return None, None
    g = np.linspace(0, 1, n, endpoint=False)
    ci = np.mean([np.interp(zc[q] + g * (zc[q + 1] - zc[q]), t, i) for q in range(len(zc) - 1)], axis=0)
    cv = np.mean([np.interp(zc[q] + g * (zc[q + 1] - zc[q]), t, v) for q in range(len(zc) - 1)], axis=0)
    return ci, cv


def shape_features(cycle_on, cycle_base, cycle_v):
    """Eigene Stromkurve des Geräts = Kurve mit Gerät minus Grundlast."""
    d = cycle_on - cycle_base
    F, V = np.fft.rfft(d), np.fft.rfft(cycle_v)
    phi = (np.degrees(np.angle(F[1]) - np.angle(V[1])) + 180) % 360 - 180
    out = {"phase": float(phi)}
    for h in HARM:
        out[f"h{h}"] = float(abs(F[h]) / abs(F[1]) * 100)
    return out, d


class Library:
    def __init__(self, path):
        self.path = path
        self.items = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []

    def save(self):
        json.dump(self.items, open(self.path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    def add(self, label, feats, source=""):
        self.items.append({"label": label, "f": {k: feats.get(k) for k in FNAMES},
                           "peak_w": feats.get("peak_w"), "step_w": feats.get("step_w"), "source": source})
        self.save()

    def labels(self):
        return sorted({it["label"] for it in self.items})

    def sigmas(self, label):
        """Streuung je Merkmal für EIN Gerät: Vorannahme, verrechnet mit der gemessenen Streuung dieses Geräts.
        Merkmale, die bei einem Gerät stark schwanken, zählen bei diesem Gerät automatisch weniger."""
        PRIOR_N = 3      # die Vorannahme zählt so viel wie drei Beispiele
        sig = {}
        for name, _, _, prior in FEATURES:
            vals = np.array([it["f"][name] for it in self.items if it["label"] == label and it["f"].get(name) is not None])
            dev = float(np.sum((vals - vals.mean()) ** 2)) if len(vals) >= 2 else 0.0
            n = max(len(vals) - 1, 0)
            sig[name] = math.sqrt((PRIOR_N * prior ** 2 + dev) / (PRIOR_N + n))
        return sig

    def classify(self, feats):
        sigs = {lab: self.sigmas(lab) for lab in self.labels()}
        best = {}
        for it in self.items:
            sig = sigs[it["label"]]
            terms = {}
            for name in FNAMES:
                a, b = feats.get(name), it["f"].get(name)
                if a is not None and b is not None:
                    terms[name] = ((a - b) / sig[name]) ** 2
            if not terms:
                continue
            d = math.sqrt(sum(terms.values()) / len(terms))
            if it["label"] not in best or d < best[it["label"]][0]:
                best[it["label"]] = (d, terms, it)
        ranking = sorted(best.items(), key=lambda kv: kv[1][0])
        if not ranking:
            return None
        w = np.array([math.exp(-0.5 * d ** 2) for _, (d, _, _) in ranking])
        conf = w / w.sum() if w.sum() > 0 else np.zeros(len(w))
        return {"ranking": [(lab, d, float(c)) for (lab, (d, _, _)), c in zip(ranking, conf)],
                "terms": {lab: terms for lab, (_, terms, _) in ranking},
                "nearest": {lab: it for lab, (_, _, it) in ranking}, "sigmas": sigs}


def explain(res, feats, unsure_dist=3.0, unknown_dist=7.0):
    """Ein Satz, warum es dieses Gerät ist und nicht das zweitbeste."""
    (lab1, d1, c1) = res["ranking"][0]
    if d1 > unknown_dist:
        return "unbekannt", f"Nichts in der Bibliothek passt (Abstand {d1:.1f}, Grenze {unknown_dist})."
    prefix = f"Unsicher (Abstand {d1:.1f}). " if d1 > unsure_dist else ""
    if len(res["ranking"]) == 1:
        return lab1, prefix + "Einziges Gerät in der Bibliothek."
    lab2 = res["ranking"][1][0]
    t1, t2 = res["terms"][lab1], res["terms"][lab2]
    common = [k for k in t1 if k in t2]
    key = max(common, key=lambda k: t2[k] - t1[k])
    disp = {f[0]: (f[1], f[2]) for f in FEATURES}
    def val(name, f):
        v = f.get(name)
        if name in ("log_peak", "log_step"):
            return f"{10 ** v:,.0f} W".replace(",", ".")
        return f"{v:.1f}{disp[name][1]}" if disp[name][1] else f"{v:.2f}"
    n1, n2 = res["nearest"][lab1]["f"], res["nearest"][lab2]["f"]
    return lab1, prefix + (f"{disp[key][0]} {val(key, feats)} passt zu {lab1} ({val(key, n1)}), "
                  f"nicht zu {lab2} ({val(key, n2)}).")
