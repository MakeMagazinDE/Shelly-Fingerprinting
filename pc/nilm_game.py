"""Werkzeug-Ratespiel: Du schaltest ein Gerät ein, der EM Gen3 misst mit 50 Hz, das Programm rät.

Aufruf:  python nilm_game.py --esp <IP-Adresse des Shelly EM Gen3>
Dann im Browser:  http://localhost:8766
"""
import argparse, csv, json, os, socket, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from nilm_core import Detector, K_WATT, BURST_AT, mean_cycle, shape_features, Library, explain, FEATURES

ap = argparse.ArgumentParser()
ap.add_argument("--esp", required=True, help="IP-Adresse des umgeflashten Shelly EM Gen3")
ap.add_argument("--port", type=int, default=5005)
ap.add_argument("--web", type=int, default=8766)
args = ap.parse_args()

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "data", time.strftime("%Y%m%d-%H%M%S") + "_spiel")
os.makedirs(OUT, exist_ok=True)
lib = Library(os.path.join(HERE, "bibliothek.json"))
lock = threading.Lock()
state = {"status": "Starte ...", "power": 0.0, "base": None, "round": None, "score": {"richtig": 0, "falsch": 0, "neu": 0},
         "devices": lib.labels(), "examples": len(lib.items)}
f_win = open(os.path.join(OUT, "windows.csv"), "w", buffering=1)
f_win.write("t,esp_ms,n,mean_ii,mean_vv,mean_iv\n")
f_rounds = open(os.path.join(OUT, "runden.csv"), "w", buffering=1, encoding="utf-8")
f_rounds.write("runde,zeit,tipp,sicherheit,abstand,wahr,merkmale\n")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", args.port)); sock.settimeout(0.5)
send = lambda c: sock.sendto(c.encode(), (args.esp, args.port))

det = Detector()
offset = None
burst_queue = []          # Zweck der angeforderten Bursts, in Reihenfolge
burst_rows, burst_tag = [], None
base_cycle, base_cycle_t = None, 0
last_base_req = 0
pending = None            # laufender Einschaltvorgang
round_no = 0


def request_burst(tag, n=1000):
    burst_queue.append(tag); send(f"BURST {n}")


def make_image(r, d_cycle, v_cycle, label, conf, tdata):
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.4), dpi=130, gridspec_kw={"width_ratios": [1.5, 1]})
    tt, pp = tdata
    ax[0].plot(tt, pp, color="#1f6f8b", lw=1)
    ax[0].axvline(0, color="#d1495b", lw=0.8, ls=":"); ax[0].axvline(BURST_AT, color="#999", lw=0.8, ls=":")
    ax[0].set_xlabel("Sekunden ab Einschalten"); ax[0].set_ylabel("W über Grundlast"); ax[0].grid(alpha=.3)
    ax[0].set_title("Einschalten mit 50 Hz", fontsize=10, loc="left")
    x = np.linspace(0, 20, len(d_cycle), endpoint=False)
    ax[1].plot(x, v_cycle / np.abs(v_cycle).max(), color="#bbb", lw=1.2, label="Spannung")
    ax[1].plot(x, d_cycle / np.abs(d_cycle).max(), color="#d1495b", lw=1.6, label="Strom des Geräts")
    ax[1].set_yticks([]); ax[1].set_xlabel("ms"); ax[1].grid(alpha=.3); ax[1].legend(fontsize=7, loc="lower left")
    ax[1].set_title("Eigene Stromkurve", fontsize=10, loc="left")
    fig.suptitle(f"Runde {r}: {label} ({conf * 100:.0f} %)", fontsize=11)
    fig.tight_layout()
    name = f"runde_{r}.png"
    fig.savefig(os.path.join(OUT, name)); plt.close(fig)
    return name


def finish_event(ev, cyc):
    """Merkmale berechnen, raten, Bild speichern."""
    global round_no
    ci, cv = cyc
    tf = det.time_features(ev["t_on"], ev["base"])
    if tf is None or base_cycle is None:
        with lock:
            state["status"] = "Messung unvollständig, bitte Gerät aus und noch mal einschalten."
        return
    sf, d = shape_features(ci, base_cycle, cv)
    feats = {**tf, **sf}
    res = lib.classify(feats) if lib.items else None
    if res:
        label, why = explain(res, feats)
        ranking = res["ranking"][:4]
        conf = ranking[0][2] if label != "unbekannt" else 0.0
        dist = ranking[0][1]
    else:
        label, why, ranking, conf, dist = "unbekannt", "Bibliothek ist leer.", [], 0.0, 99.0
    t = np.array(det.t); p = np.array(det.p) - ev["base"]
    m = (t >= ev["t_on"] - 1.5) & (t <= ev["t_on"] + 6)
    round_no += 1
    img = make_image(round_no, d, cv, label, conf, (t[m] - ev["t_on"], p[m]))
    with lock:
        state["round"] = {"nr": round_no, "label": label, "conf": conf, "why": why, "dist": round(dist, 2),
                          "ranking": [(l, round(c * 100)) for l, _, c in ranking], "img": img,
                          "peak": round(tf["peak_w"]), "step": round(tf["step_w"]), "settle": round(tf["settle"], 2),
                          "phase": round(sf["phase"], 1), "h3": round(sf["h3"], 1), "h5": round(sf["h5"], 1),
                          "feedback": None, "feats": feats}
        state["status"] = "Geraten. War das richtig?"
    print(f"Runde {round_no}: {label} ({conf * 100:.0f} %) – {why}")


def on_feedback(nr, truth):
    with lock:
        r = state["round"]
        if not r or r["nr"] != nr or r["feedback"] is not None:
            return
        guess = r["label"]
        if truth == "OK":
            truth = guess
        if truth == "x":
            r["feedback"] = "verworfen"; return
        if truth == "unbekannt":
            return
        new = truth not in lib.labels()
        lib.add(truth, r["feats"], source=f"Spiel Runde {nr}")
        if guess == truth:
            state["score"]["richtig"] += 1; r["feedback"] = "richtig"
        elif new and guess == "unbekannt":
            state["score"]["neu"] += 1; r["feedback"] = f"neu gelernt: {truth}"
        else:
            state["score"]["falsch"] += 1; r["feedback"] = f"falsch, war {truth}"
        state["devices"] = lib.labels(); state["examples"] = len(lib.items)
        f_rounds.write(f"{nr},{datetime.now():%H:%M:%S},{guess},{r['conf']:.2f},{r['dist']},{truth},"
                       f"\"{json.dumps({k: round(v, 3) for k, v in r['feats'].items()})}\"\n")


def udp_loop():
    global offset, burst_rows, burst_tag, base_cycle, base_cycle_t, last_base_req, pending
    send("HELLO"); send("CH B"); last_hello = time.time()
    while True:
        if time.time() - last_hello > 3:
            send("HELLO"); last_hello = time.time()
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        now = time.time()
        for line in data.decode(errors="replace").splitlines():
            q = line.split(",")
            if q[0] == "S":
                o = now - float(q[1]) / 1000
                offset = o if offset is None else min(offset, o)
            elif q[0] == "W" and len(q) == 7 and offset is not None and int(q[2]) >= 20:
                t = float(q[1]) / 1000 + offset
                pw = float(q[5]) * K_WATT
                f_win.write(f"{t:.3f}," + ",".join(q[1:6]) + "\n")
                for kind, te, base in det.feed(t, pw):
                    if kind == "on" and pending is None:
                        pending = {"t_on": te, "base": base, "burst_sent": False, "cycle": None}
                        with lock:
                            state["status"] = "Gerät erkannt, messe ..."
                    elif kind in ("off", "steady"):
                        with lock:
                            if state["status"].startswith("Gerät erkannt"):
                                state["status"] = "Zu kurz eingeschaltet, bitte mindestens 3 Sekunden laufen lassen."
                        if pending and not pending["burst_sent"]:
                            pending = None
                with lock:
                    state["power"] = round(pw, 1); state["base"] = det.base and round(det.base, 1)
                if pending and not pending["burst_sent"] and t >= pending["t_on"] + BURST_AT:
                    pending["burst_sent"] = True; request_burst("event")
                # frische Grundlast-Kurve, wenn ruhig
                if pending is None and det.state == "idle" and now - last_base_req > 30:
                    w = np.array(det.p[-75:])
                    if len(w) == 75 and np.percentile(w, 90) - np.percentile(w, 10) < 30:
                        last_base_req = now; request_burst("base")
                        with lock:
                            if state["round"] is None:
                                state["status"] = "Bereit. Schalte ein Gerät ein und lass es 3 Sekunden im Leerlauf laufen."
            elif q[0] == "BSTART":
                burst_rows, burst_tag = [], (burst_queue.pop(0) if burst_queue else None)
            elif q[0] == "B":
                burst_rows.append(q[1:4])
            elif q[0] == "BEND":
                cyc = mean_cycle([r[0] for r in burst_rows], [r[1] for r in burst_rows], [r[2] for r in burst_rows])
                if cyc[0] is None:
                    continue
                if burst_tag == "base":
                    base_cycle, base_cycle_t = cyc[0], now
                elif burst_tag == "event" and pending:
                    ev, pending = pending, None
                    threading.Thread(target=finish_event, args=(ev, cyc), daemon=True).start()


PAGE = """<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Werkzeug-Ratespiel</title><style>
:root{--bg:#f6f5f2;--fg:#1d2329;--mut:#6b7280;--card:#fff;--acc:#1f6f8b;--ok:#2e7d32;--bad:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#15181c;--fg:#e8eaed;--mut:#9aa0a6;--card:#1f2328;--acc:#5fb3d0}}
body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:980px;margin:0 auto;padding:16px}
.card{background:var(--card);border-radius:12px;padding:16px;margin-bottom:12px;box-shadow:0 1px 3px #0002}
#guess{font-size:44px;font-weight:700;margin:4px 0}#why{color:var(--mut)}
.bar{display:flex;align-items:center;gap:8px;margin:3px 0}.bar span{width:150px}.bar div{height:14px;background:var(--acc);border-radius:7px}
button{font-size:16px;padding:8px 14px;margin:4px;border-radius:8px;border:1px solid var(--mut);background:var(--card);color:var(--fg);cursor:pointer}
button.ok{background:var(--ok);color:#fff;border:none}img{max-width:100%;border-radius:8px}
.row{display:flex;gap:24px;flex-wrap:wrap;color:var(--mut)}#live{font-size:22px}
input{font-size:16px;padding:8px;border-radius:8px;border:1px solid var(--mut)}
</style></head><body><main>
<div class=card><div id=status></div><div id=live></div><div class=row id=score></div></div>
<div class=card id=rcard style="display:none"><div style="color:var(--mut)" id=rnr></div><div id=guess></div><div id=why></div>
<div id=bars style="margin:10px 0"></div><div class=row id=feat></div>
<div id=fb style="margin-top:10px"></div><img id=img></div>
<script>
let cur=null;
async function fb(l){await fetch('/feedback?nr='+cur+'&label='+encodeURIComponent(l));tick()}
async function tick(){const s=await (await fetch('/state.json')).json();
document.getElementById('status').textContent=s.status;
document.getElementById('live').textContent='Jetzt: '+s.power.toFixed(0)+' W   (Grundlast '+(s.base??'–')+' W)';
document.getElementById('score').innerHTML='<span>richtig '+s.score.richtig+'</span><span>falsch '+s.score.falsch+'</span><span>neu gelernt '+s.score.neu+'</span><span>Bibliothek: '+s.examples+' Beispiele, '+s.devices.length+' Geräte</span>';
const r=s.round;if(!r)return;document.getElementById('rcard').style.display='block';
document.getElementById('rnr').textContent='Runde '+r.nr;
document.getElementById('guess').textContent=r.label+(r.label!=='unbekannt'?'  '+Math.round(r.conf*100)+' %':'');
document.getElementById('why').textContent=r.why;
document.getElementById('bars').innerHTML=r.ranking.map(x=>'<div class=bar><span>'+x[0]+'</span><div style="width:'+(x[1]*3+2)+'px"></div>'+x[1]+' %</div>').join('');
document.getElementById('feat').innerHTML='<span>Anlaufspitze '+r.peak+' W</span><span>danach '+r.step+' W</span><span>Anlaufdauer '+r.settle+' s</span><span>Phase '+r.phase+'°</span><span>H3 '+r.h3+' %</span><span>H5 '+r.h5+' %</span>';
if(cur!==r.nr||document.getElementById('img').dataset.n!=r.nr){document.getElementById('img').src='/img/'+r.img;document.getElementById('img').dataset.n=r.nr}
cur=r.nr;const f=document.getElementById('fb');
if(r.feedback){f.innerHTML='<b>'+r.feedback+'</b>'}else if(f.dataset.n!=r.nr){f.dataset.n=r.nr;
f.innerHTML=(r.label!=='unbekannt'?'<button class=ok onclick="fb(\\'OK\\')">Richtig</button>':'')+'<span style="color:var(--mut)">Es war: </span>'+
s.devices.map(d=>'<button onclick="fb(\\''+d+'\\')">'+d+'</button>').join('')+
' <input id=neu placeholder="neues Gerät"><button onclick="fb(document.getElementById(\\'neu\\').value)">lernen</button> <button onclick="fb(\\'x\\')">verwerfen</button>'}}
setInterval(tick,1000);tick();
</script></main></body></html>"""


class Web(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            body, ct = PAGE.encode(), "text/html; charset=utf-8"
        elif u.path == "/state.json":
            with lock:
                s = dict(state); r = dict(s["round"]) if s["round"] else None
                if r: r.pop("feats", None)
                s["round"] = r
            body, ct = json.dumps(s, ensure_ascii=False).encode(), "application/json"
        elif u.path.startswith("/img/"):
            fp = os.path.join(OUT, os.path.basename(u.path))
            if not os.path.exists(fp):
                self.send_response(404); self.end_headers(); return
            body, ct = open(fp, "rb").read(), "image/png"
        elif u.path == "/feedback":
            q = parse_qs(u.query)
            label = q.get("label", [""])[0].strip()
            if label:
                on_feedback(int(q.get("nr", ["0"])[0]), label)
            body, ct = b"ok", "text/plain"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


threading.Thread(target=udp_loop, daemon=True).start()
print(f"Spiel läuft: http://localhost:{args.web}   Daten: {os.path.abspath(OUT)}")
ThreadingHTTPServer(("127.0.0.1", args.web), Web).serve_forever()
