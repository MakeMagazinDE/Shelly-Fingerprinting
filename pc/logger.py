"""Empfängt die 50-Hz-Daten des umgeflashten Shelly EM Gen3 und schreibt sie als CSV.

Aufruf:  python logger.py <name> --esp <IP des EM Gen3> [--ref <IP eines Shelly Plug>]
  --ref   IP eines Shelly Plug (Stock-Firmware), der dasselbe Werkzeug misst.
          Liefert die 1-Hz-Vergleichskurve und die Kalibrierung.
Tasten (Enter drücken): a = Kanal A, b = Kanal B, burst = Rohkurve aufnehmen, q = Ende
"""
import argparse, json, os, socket, sys, threading, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("name")
ap.add_argument("--ref", default=None, help="Plug-IP, oder em3:<IP>:<phase> für einen Shelly Pro 3EM")
ap.add_argument("--chan", default="B", help="Stromkanal am Chip (A/B)")
ap.add_argument("--port", type=int, default=5005)
ap.add_argument("--esp", default=None, help="IP des EM Gen3, falls bekannt")
args = ap.parse_args()

outdir = os.path.join(os.path.dirname(__file__), "..", "data", time.strftime("%Y%m%d-%H%M%S") + "_" + args.name)
os.makedirs(outdir, exist_ok=True)
f_win = open(os.path.join(outdir, "windows.csv"), "w", buffering=1)
f_win.write("pc_time,esp_ms,n,mean_ii,mean_vv,mean_iv,i_peak\n")
f_stat = open(os.path.join(outdir, "status.csv"), "w", buffering=1)
f_stat.write("pc_time,esp_ms,irms_a,irms_b,vrms,awatt,bwatt,pairs_per_s,chan,i2c_err,rssi\n")
f_ref = open(os.path.join(outdir, "reference.csv"), "w", buffering=1) if args.ref else None
if f_ref:
    f_ref.write("pc_time,apower,voltage,current\n")

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", args.port))
sock.settimeout(0.5)

esp = args.esp
running = True
burst_no = 0
burst_file = None
last_stat = ""
win_count = 0


def send(cmd):
    if esp:
        sock.sendto(cmd.encode(), (esp, args.port))
        print(f"-> {cmd}")
    else:
        print("ESP noch nicht gefunden")


def keyboard():
    global running
    for line in sys.stdin:
        c = line.strip().lower()
        if c == "a":
            send("CH A")
        elif c == "b":
            send("CH B")
        elif c.startswith("burst"):
            send("BURST 2000")
        elif c == "q":
            running = False
            break


CMD_FILE = os.path.join(os.path.dirname(__file__), "cmd.txt")


def command_file():
    global running
    while running:
        if os.path.exists(CMD_FILE):
            try:
                c = open(CMD_FILE).read().strip()
                os.remove(CMD_FILE)
            except OSError:
                c = ""
            if c == "q":
                running = False
            elif c:
                send(c)
        time.sleep(0.3)


def hello():
    while running:
        if esp:
            sock.sendto(b"HELLO", (esp, args.port))
        time.sleep(3)


def reference():
    last = None
    em3 = args.ref.startswith("em3:")
    if em3:
        _, ip, ph = args.ref.split(":")
        url = f"http://{ip}/rpc/EM.GetStatus?id=0"
    else:
        url = f"http://{args.ref}/rpc/Switch.GetStatus?id=0"
    while running:
        try:
            d = json.load(urllib.request.urlopen(url, timeout=1))
            if em3:
                row = (d[f"{ph}_act_power"], d[f"{ph}_voltage"], d[f"{ph}_current"])
            else:
                row = (d["apower"], d["voltage"], d["current"])
            if row != last:
                f_ref.write(f"{time.time():.3f},{row[0]},{row[1]},{row[2]}\n")
                last = row
        except Exception:
            pass
        time.sleep(0.2)


if esp:
    sock.sendto(b"HELLO", (esp, args.port))
    sock.sendto(f"CH {args.chan.upper()}".encode(), (esp, args.port))
    print(f"EM Gen3 direkt angesprochen: {esp}")
threading.Thread(target=keyboard, daemon=True).start()
threading.Thread(target=hello, daemon=True).start()
threading.Thread(target=command_file, daemon=True).start()
if args.ref:
    threading.Thread(target=reference, daemon=True).start()

print(f"Schreibe nach {os.path.abspath(outdir)}")
print("Warte auf den EM Gen3 ... (Tasten: a / b / burst / q)")
t_print = time.time()
while running:
    try:
        data, addr = sock.recvfrom(4096)
    except socket.timeout:
        continue
    now = time.time()
    if esp is None:
        esp = addr[0]
        print(f"EM Gen3 gefunden: {esp}")
        sock.sendto(b"HELLO", (esp, args.port))
        sock.sendto(f"CH {args.chan.upper()}".encode(), (esp, args.port))
    for line in data.decode(errors="replace").splitlines():
        p = line.split(",")
        if p[0] == "W" and len(p) == 7:
            f_win.write(f"{now:.3f}," + ",".join(p[1:]) + "\n")
            win_count += 1
        elif p[0] == "S":
            f_stat.write(f"{now:.3f}," + ",".join(p[1:]) + "\n")
            last_stat = line
        elif p[0] == "BSTART":
            burst_no += 1
            burst_file = open(os.path.join(outdir, f"burst_{burst_no}.csv"), "w")
            burst_file.write("us,i,v\n")
            print(f"Burst {burst_no}: {p[1]} Messpaare, Kanal {p[2]}")
        elif p[0] == "B" and burst_file:
            burst_file.write(",".join(p[1:]) + "\n")
        elif p[0] == "BEND" and burst_file:
            burst_file.close()
            burst_file = None
            print(f"Burst {burst_no} gespeichert")
    if now - t_print >= 1:
        if last_stat:
            s = last_stat.split(",")
            print(f"Fenster/s {win_count:3d} | Messpaare/s {s[7]} | Kanal {s[8]} | IRMS A {s[2]} B {s[3]} | VRMS {s[4]} | I2C-Fehler {s[9]} | RSSI {s[10]}")
        win_count = 0
        t_print = now

print("Ende.")
