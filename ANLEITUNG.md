# Kurzanleitung

**Achtung:** Die Platine des Shelly EM Gen3 liegt auf Netzpotenzial, sobald er am Netz hängt, und die Programmierkontakte sind nicht isoliert. Flashen nur ohne Netzanschluss, messen nur bei geschlossenem Gehäuse.

## Firmware (Ordner `firmware`)

1. `include/secrets.example.h` nach `include/secrets.h` kopieren und WLAN-Name und Passwort eintragen (nur 2,4 GHz).
2. USB-Seriell-Adapter auf 3,3 Volt an die Buchse J6 anschließen: TXD an Pin 3, RXD an Pin 2, GND an Pin 7, VCC an Pin 4. Pin 6 auf Masse legen, dann erst USB einstecken.
3. Originalfirmware sichern und prüfen:
   `python -m esptool --port COMx --before no_reset read_flash 0 0x800000 original.bin`
   `python -m esptool --port COMx --before no_reset verify_flash 0 original.bin`
4. Flash löschen und neue Firmware aufspielen:
   `python -m esptool --port COMx --before no_reset erase_flash`
   `pio run -e emgen3 -t upload --upload-port COMx`
5. Brücke an Pin 6 entfernen, USB kurz trennen. Weitere Updates per WLAN: IP-Adresse in `platformio.ini` eintragen, dann `pio run -e emgen3_ota -t upload`.

Die Firmware liest Strom und Spannung aus dem ADE7953 (rund 2170 Messpaare pro Sekunde), schickt pro Netzperiode Effektivwerte und Wirkleistung per UDP (Port 5005) und nimmt auf Befehl Rohkurven auf. Die Zange hängt standardmäßig an Kanal B des Chips, per UDP-Befehl `CH A` lässt sich umschalten.

## PC-Programme (Ordner `pc`)

`pip install -r requirements.txt`

| Programm | Aufgabe |
|---|---|
| `logger.py <name> --esp <IP>` | schreibt die 50-Hz-Werte als CSV, optional mit `--ref` einen Shelly als Vergleich |
| `plot_tool.py <sitzungsordner>` | Lastkurve in drei Abtastraten (50 Hz, 1 Hz, 8 s) und Oberwellen der Rohkurven |
| `compare_tools.py <sitzung> "Name:3,4" ...` | vergleicht die Stromkurven mehrerer Geräte |
| `nilm_game.py --esp <IP>` | Ratespiel im Browser unter http://localhost:8766 |
| `simulate.py` | spielt Shelly und Werkzeug vor, zum Testen ohne Hardware |

Das Ratespiel startet mit leerer Bibliothek und lernt aus jeder Runde. Wer mit Beispieldaten starten möchte, kopiert `bibliothek_beispiel.json` nach `bibliothek.json`. Die Beispiele stammen aus der Werkstatt des Autors und passen nur zu denselben Geräten.
