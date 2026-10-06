"""Testet logger.py und plot_tool.py ohne Hardware: spielt EM Gen3 und einen Shelly Plug vor."""
import json, math, random, socket, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

T0 = time.time()
K = 0.002          # "Rohwert" -> Watt


def watts(t):
    t -= 5
    if t < 0 or t > 20:
        return 0.0
    if t < 0.15:
        return 2300 * (1 - t / 0.15) + 785 * t / 0.15
    return 785 + (135 if 8 < t < 12 else 0) + random.gauss(0, 8)


class Ref(BaseHTTPRequestHandler):
    def do_GET(self):
        t = math.floor(time.time() - T0)
        p = sum(watts(t + k / 50) for k in range(50)) / 50
        body = json.dumps({"apower": round(p, 1), "voltage": 231.0, "current": round(p / 231, 3)}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


threading.Thread(target=HTTPServer(("127.0.0.1", 8765), Ref).serve_forever, daemon=True).start()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", 5006))
dst = ("127.0.0.1", 5005)
buf, last_stat, burst_sent = [], 0, False
while time.time() - T0 < 32:
    t = time.time() - T0
    p = watts(t) / K
    vv = (231 / 0.005) ** 2
    buf.append(f"W,{int(t*1000)},28,{p*p/vv*4:.6g},{vv:.6g},{p:.6g},{int(math.sqrt(p))}")
    if len(buf) >= 25:
        s.sendto(("\n".join(buf) + "\n").encode(), dst); buf = []
    if t - last_stat >= 1:
        s.sendto(f"S,{int(t*1000)},{int(p)},0,46200,{int(p)},0,1400,A,0,-60\n".encode(), dst); last_stat = t
    if t > 15 and not burst_sent:
        lines = ["BSTART,1500,A"]
        for k in range(1500):
            us = int(k * 1e6 / 1400)
            ph = 2 * math.pi * 50 * us / 1e6
            i = 100000 * (math.sin(ph) + 0.3 * math.sin(3 * ph) + 0.12 * math.sin(5 * ph))
            lines.append(f"B,{us},{int(i)},{int(300000*math.sin(ph))}")
        lines.append("BEND")
        for k in range(0, len(lines), 40):
            s.sendto(("\n".join(lines[k:k+40]) + "\n").encode(), dst); time.sleep(0.002)
        burst_sent = True
    time.sleep(0.02)
print("Simulation fertig")
