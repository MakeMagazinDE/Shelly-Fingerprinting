// Shelly EM Gen3 (ESP32-C3 + ADE7953) — schnelle Lastkurven statt 1 Hz.
// Liest die Rohkurven von Strom und Spannung (ADE7953, intern 6,99 kHz) so schnell
// wie I2C erlaubt, rechnet pro 20-ms-Fenster (eine Netzperiode) Effektivstrom,
// Effektivspannung und Wirkleistung und schickt die Rohsummen per UDP an den PC.
// Skalierung auf A/V/W passiert am PC (Kalibrierung gegen eine Referenzmessung).

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <ArduinoOTA.h>
#include "secrets.h"

static const uint8_t ADE_ADDR = 0x38;
static const int PIN_SCL = 6, PIN_SDA = 7, PIN_ADE_RESET = 5, PIN_LED = 9;
static const uint16_t UDP_PORT = 5005;

// ADE7953-Register (24-Bit-Varianten)
static const uint16_t REG_IA = 0x216, REG_IB = 0x217, REG_V = 0x218;
static const uint16_t REG_AWATT = 0x212, REG_BWATT = 0x213;
static const uint16_t REG_IRMSA = 0x21A, REG_IRMSB = 0x21B, REG_VRMS = 0x21C;

WiFiUDP udp;
IPAddress pcIp;
bool havePc = false;
uint16_t pcPort = UDP_PORT;
bool useB = true;              // Stromkanal am Chip; A und B sind beim EM Gen3 laut ESPHome vertauscht beschriftet, per UDP-Befehl "CH A"/"CH B" umschaltbar
bool resetWindow = false;      // nach einem Burst das laufende 20-ms-Fenster verwerfen
uint32_t i2cErrors = 0;

static bool adeWrite(uint16_t reg, uint32_t val, uint8_t nbytes) {
  Wire.beginTransmission(ADE_ADDR);
  Wire.write(reg >> 8);
  Wire.write(reg & 0xFF);
  for (int b = nbytes - 1; b >= 0; b--) Wire.write((val >> (8 * b)) & 0xFF);
  return Wire.endTransmission() == 0;
}

static bool adeRead24(uint16_t reg, bool isSigned, int32_t &out) {
  Wire.beginTransmission(ADE_ADDR);
  Wire.write(reg >> 8);
  Wire.write(reg & 0xFF);
  if (Wire.endTransmission(false) != 0) { i2cErrors++; return false; }
  if (Wire.requestFrom(ADE_ADDR, (uint8_t)3) != 3) { i2cErrors++; return false; }
  uint32_t b2 = Wire.read();
  uint32_t b1 = Wire.read();
  uint32_t b0 = Wire.read();
  uint32_t v = (b2 << 16) | (b1 << 8) | b0;
  if (isSigned && (v & 0x800000)) v |= 0xFF000000;
  out = (int32_t)v;
  return true;
}

static void adeInit() {
  pinMode(PIN_ADE_RESET, OUTPUT);
  digitalWrite(PIN_ADE_RESET, LOW);
  delay(20);
  digitalWrite(PIN_ADE_RESET, HIGH);
  delay(150);
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  // Pflicht laut Datenblatt nach jedem Einschalten: 0xAD nach 0xFE, dann 0x30 nach 0x120
  adeWrite(0x0FE, 0xAD, 1);
  adeWrite(0x120, 0x0030, 2);
}

static void sendText(const char *s, size_t len) {
  if (havePc) udp.beginPacket(pcIp, pcPort);
  else udp.beginPacket(IPAddress(255, 255, 255, 255), UDP_PORT);
  udp.write((const uint8_t *)s, len);
  udp.endPacket();
}

// --- Burst: kurze Rohkurve für die Wellenform ---
static const int BURST_MAX = 3000;
static int32_t bI[BURST_MAX], bV[BURST_MAX];
static uint32_t bUs[BURST_MAX];

static void doBurst(int n) {
  if (n > BURST_MAX) n = BURST_MAX;
  uint16_t regI = useB ? REG_IB : REG_IA;
  uint32_t t0 = micros();
  int got = 0;
  for (int k = 0; k < n; k++) {
    int32_t i, v;
    uint32_t t = micros() - t0;
    if (!adeRead24(regI, true, i)) continue;
    if (!adeRead24(REG_V, true, v)) continue;
    bI[got] = i; bV[got] = v; bUs[got] = t; got++;
  }
  char buf[1400];
  size_t len = snprintf(buf, sizeof(buf), "BSTART,%d,%c\n", got, useB ? 'B' : 'A');
  for (int k = 0; k < got; k++) {
    char line[48];
    int l = snprintf(line, sizeof(line), "B,%lu,%ld,%ld\n", (unsigned long)bUs[k], (long)bI[k], (long)bV[k]);
    if (len + l >= sizeof(buf)) { sendText(buf, len); len = 0; delay(2); }
    memcpy(buf + len, line, l); len += l;
  }
  len += snprintf(buf + len, sizeof(buf) - len, "BEND\n");
  sendText(buf, len);
}

static void handleCommands() {
  int sz = udp.parsePacket();
  if (sz <= 0) return;
  char cmd[64] = {0};
  udp.read(cmd, sizeof(cmd) - 1);
  pcIp = udp.remoteIP();
  pcPort = udp.remotePort();
  havePc = true;
  if (strncmp(cmd, "CH A", 4) == 0) useB = false;
  else if (strncmp(cmd, "CH B", 4) == 0) useB = true;
  else if (strncmp(cmd, "BURST", 5) == 0) doBurst(atoi(cmd + 6) > 0 ? atoi(cmd + 6) : 2000);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, HIGH);
  adeInit();
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("emgen3-fast");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(250); Serial.print('.'); }
  WiFi.setSleep(false);
  Serial.printf("\nWLAN ok, IP %s\n", WiFi.localIP().toString().c_str());
  ArduinoOTA.setHostname("emgen3-fast");
  ArduinoOTA.begin();
  udp.begin(UDP_PORT);
}

// Fenster-Akkumulatoren
static double sII = 0, sVV = 0, sIV = 0;
static int32_t iPeak = 0;
static uint32_t nPairs = 0, winStartUs = 0, lastStatusMs = 0, pairsLastSec = 0;
static bool flip = false;
static char wbuf[1400];
static size_t wlen = 0;

void loop() {
  if (winStartUs == 0 || resetWindow) {
    sII = sVV = sIV = 0; iPeak = 0; nPairs = 0;
    winStartUs = micros(); resetWindow = false;
  }

  // Ein Messpaar, Reihenfolge wechselt, damit sich der Zeitversatz zwischen I und V herausmittelt
  int32_t i, v;
  uint16_t regI = useB ? REG_IB : REG_IA;
  bool ok = flip ? (adeRead24(REG_V, true, v) && adeRead24(regI, true, i))
                 : (adeRead24(regI, true, i) && adeRead24(REG_V, true, v));
  flip = !flip;
  if (ok) {
    double di = i, dv = v;
    sII += di * di; sVV += dv * dv; sIV += di * dv;
    if (abs(i) > iPeak) iPeak = abs(i);
    nPairs++; pairsLastSec++;
  }

  uint32_t nowUs = micros();
  if (nowUs - winStartUs >= 20000) {          // 20 ms = eine Netzperiode bei 50 Hz
    if (nPairs > 0 && havePc) {
      int l = snprintf(wbuf + wlen, sizeof(wbuf) - wlen, "W,%lu,%lu,%.6g,%.6g,%.6g,%ld\n",
                       (unsigned long)millis(), (unsigned long)nPairs,
                       sII / nPairs, sVV / nPairs, sIV / nPairs, (long)iPeak);
      if (l > 0) wlen += l;
    }
    sII = sVV = sIV = 0; iPeak = 0; nPairs = 0;
    winStartUs = nowUs;
    if (wlen > sizeof(wbuf) - 120) { sendText(wbuf, wlen); wlen = 0; }

    handleCommands();
    ArduinoOTA.handle();

    uint32_t nowMs = millis();
    if (nowMs - lastStatusMs >= 1000) {
      int32_t ra = 0, rb = 0, rv = 0, pa = 0, pb = 0;
      adeRead24(REG_IRMSA, false, ra); adeRead24(REG_IRMSB, false, rb);
      adeRead24(REG_VRMS, false, rv);
      adeRead24(REG_AWATT, true, pa); adeRead24(REG_BWATT, true, pb);
      char s[200];
      int l = snprintf(s, sizeof(s), "S,%lu,%ld,%ld,%ld,%ld,%ld,%lu,%c,%lu,%d\n",
                       (unsigned long)nowMs, (long)ra, (long)rb, (long)rv, (long)pa, (long)pb,
                       (unsigned long)pairsLastSec, useB ? 'B' : 'A', (unsigned long)i2cErrors, WiFi.RSSI());
      if (wlen) { sendText(wbuf, wlen); wlen = 0; }
      sendText(s, l);
      pairsLastSec = 0;
      lastStatusMs = nowMs;
      digitalWrite(PIN_LED, !digitalRead(PIN_LED));
    }
  }
}
