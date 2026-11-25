# Lernpfad: PressWatch verstehen und erklären

Dieses Dokument ist für dich, Arif. Im Gespräch zählt nicht, dass das Projekt existiert, sondern dass du jede Entscheidung erklären kannst. Rechne mit 3–4 Abenden.

## 1. In welcher Reihenfolge lesen

| Abend | Dateien | Ziel |
|---|---|---|
| 1 | `sim/process.py`, `sim/scenario.py`, `tests/test_process.py` | Wie entsteht eine Linse? Was sind Verschleiß und Sensorfehler? |
| 1 | `features.py`, `quality.py` | Was wird aus den Kurven berechnet, was wird geprüft? |
| 2 | `models/hybrid.py`, `models/baseline.py`, `models/__init__.py`, `tests/test_models.py` | Warum ist das Hybridmodell besser? Was steht in der Modellkarte? |
| 2 | `monitoring/drift.py`, `monitoring/performance.py`, `monitoring/decision.py` | Wie erkennt PressWatch Drift und die Ursache? |
| 3 | `api/services.py`, `api/app.py`, `db/models.py` | Was passiert bei jedem Request, was steht in der Datenbank? |
| 3 | `opcua/cell.py`, `opcua/collector.py` | Wie kommt ein Zyklus von der Maschine in die API? |
| 4 | `web/static/app.js`, `charts.js` | Wie entsteht das Dashboard? |
| 4 | `docker-compose.yml`, `deploy/nginx.conf`, `.github/workflows/ci.yml` | Wie läuft alles getrennt? |
| 4 | `static_site.py`, `web/static/replay.js` | Wie wird ein Lauf aufgezeichnet und auf Netlify abgespielt? |

Lauf bei jedem Teil die passenden Tests mit `pytest tests/test_<teil>.py -v` und ändere absichtlich etwas, damit ein Test rot wird. So siehst du, was der Test wirklich prüft.

## 2. Begriffe in einfachen Worten

- **Nicht-isothermes Glaspressen (NGM):** Der Glasposten ist heißer als die Werkzeuge. Das Glas kühlt beim Pressen ab und wird schnell zäh. Das ist schneller als isothermes Pressen, aber schwerer zu beherrschen.
- **Mittendicke und PV:** Mittendicke = Dicke der Linse in der Mitte. PV (peak-to-valley) = größter Abstand zwischen Berg und Tal der Abweichung von der Sollform.
- **VFT-Gleichung:** Wie stark die Viskosität von Glas mit der Temperatur steigt. Schon 20 °C weniger machen das Glas viel zäher.
- **Stefan-Quetschströmung:** Wie schnell eine zähe Scheibe zwischen zwei Platten dünner wird. Mehr Kraft, weniger Viskosität, mehr Zeit: dünner.
- **OPC UA:** Standard-Protokoll, mit dem Maschinen Daten anbieten. Die Maschine ist der Server mit einem Adressraum (Ordner und Variablen). Der Collector ist ein Client.
- **Subscription:** Der Client sagt dem Server: „Melde dich, wenn sich der Zykluszähler ändert.“ Dann muss er nicht ständig nachfragen.
- **Latin Hypercube:** Versuchsplan, bei dem jeder Parameter seinen Bereich gleichmäßig abdeckt, mit wenigen Versuchen.
- **Hybridmodell:** Physikalisches Wissen liefert Merkmale (z. B. geschätzte Dicke nach Stefan), ein einfaches ML-Modell korrigiert und gewichtet sie. Deshalb braucht es weniger Daten und extrapoliert besser.
- **Lasso:** Lineare Regression, die unwichtige Merkmale auf null setzt. Die Gewichte stehen in der Modellkarte.
- **PSI (Population Stability Index):** Wie stark sich eine Verteilung verändert hat. Faustregel: über 0,25 = deutlich.
- **KS-Test:** Prüft, ob zwei Stichproben aus derselben Verteilung stammen könnten. Kleiner p-Wert = vermutlich nicht.
- **CUSUM:** Summiert kleine, dauerhafte Abweichungen auf. Einzelne Ausreißer gehen unter, ein langsamer Trend fällt nach einer Weile auf.
- **Residuum:** gemessen minus vorhergesagt.
- **Modellkarte:** Steckbrief eines Modells: Daten, Kennzahlen, Gültigkeitsbereich, Grenzen.
- **ISO/IEC 42001:** Norm für KI-Managementsysteme. PressWatch erfüllt sie nicht, greift aber die Idee auf: nachvollziehbar, wer welches Modell wann warum eingesetzt hat.

## 3. Zahlen, die du auswendig wissen solltest

- 150 Einfahrversuche (Latin Hypercube), 5 Prozessparameter wie bei Mende et al. (2026).
- Hybrid gegen Black-Box mit 30 Versuchen: Mittendicke 28 µm gegen 135 µm Fehler, PV 0,40 µm gegen 0,65 µm.
- Überwachung: 0 Alarme in 5 normalen Läufen (7500 Zyklen), 1 Warnung. Verschleiß 56–174 Zyklen vor dem Ausschuss erkannt. Sensorfehler immer richtig als Sensor erkannt.
- CUSUM: Referenz 60 Messungen, k = 0,75, Schwelle 10. Mit 30 Messungen: 9 von 12 Läufen mit Fehlalarm.
- Robustheit: 15 s API-Ausfall → 60 Zyklen gepuffert, 0 verloren.
- Tests: 98, davon 3 gegen PostgreSQL, 2 mit echten OPC-UA- und HTTP-Servern und 8 für die Netlify-Seite.

## 4. Fragen, die kommen könnten

**„Warum simuliert und nicht echte Daten?“**
Weil ich keine Presse habe. Der Simulator hat aber einen Vorteil: Ich kenne die Wahrheit. Deshalb kann ich messen, ob die Überwachung einen Fehler wirklich erkennt und wann. Mit echten Daten würde ich das Prozessmodell durch die Maschine ersetzen, der Rest bleibt.

**„Warum ist das Hybridmodell besser?“**
Es muss die Physik nicht aus Daten lernen. Die Expertenmerkmale (Glastemperatur, Viskosität, geschätzte Dicke) tragen schon fast alles. Lasso korrigiert nur noch. Die Black-Box muss den stark nichtlinearen Zusammenhang aus wenigen Punkten erraten. Die Expertenkonstanten sind absichtlich falsch, damit das fair bleibt.

**„Warum nicht automatisch neu trainieren?“**
Bei einem Sensorfehler würde das Modell falsche Physik lernen. Bei Verschleiß hilft Retraining nur kurz, das Werkzeug muss gewechselt werden. Deshalb schlägt PressWatch vor, und ein Mensch entscheidet mit Begründung.

**„Woher weiß das System die Ursache?“**
Aus der Kombination: Ändern sich nur die Fehler, aber nicht die Eingangsdaten, hat sich etwas geändert, das kein Sensor misst (Verschleiß). Ändern sich beide, liefert vermutlich ein Sensor falsche Werte. Das ist eine Vermutung, deshalb steht „wahrscheinlich“ da.

**„Warum Ist minus Soll?“**
Weil es drei Rezepte gibt. Die Rohwerte springen bei jedem Rezeptwechsel. Ist minus Soll bleibt stabil, solange die Maschine gesund ist.

**„Was war am schwierigsten?“**
Fehlalarme. Zuerst hat die Überwachung in 9 von 12 normalen Läufen Alarm gegeben, weil die Referenzphase zu kurz war und das Modell je Rezept unterschiedlich danebenlag. Mit Normierung je Rezept und 60 Messungen Referenz waren es 0 von 12.

**„Wie würdest du das an eure IQP anbinden?“**
Collector und Datenqualität sind unabhängig vom Modell. Die Modelle haben eine einfache Schnittstelle (fit/predict). Ich würde fragen, wie die IQP Modelle deployt (Container?) und Daten speichert, und dann Collector und Überwachung als Dienste anbinden.

**„Was würdest du als Nächstes bauen?“**
Eine Bayes'sche Optimierung der Rezepte mit einem Dashboard, das die Vorschläge erklärt, wie in eurem Paper von Leyendecker et al. Oder die Werker-Ansicht auf echten Smart Glasses.

**„Hast du das allein gebaut?“**
Sei ehrlich. Wenn du KI-Werkzeuge benutzt hast, sag es. In deinem Lebenslauf steht ohnehin „Interesse an KI-gestützter Softwareentwicklung“. Entscheidend ist, dass du jede Zeile erklären und selbst erweitern kannst, deshalb Abschnitt 5.

## 5. Drei Erweiterungen, die du selbst baust

Bau mindestens eine davon selbst, bevor du das Projekt verschickst. Dann ist es wirklich deins, und du hast eine echte Geschichte für das Gespräch.

1. **Neues Fehlerszenario „Ofen driftet“** (leicht, 2–3 Stunden)
   - In `sim/process.py` gibt es schon `MachineState.furnace_offset_c`. Bau in `FaultSettings` und `_apply_faults` eine Rampe dafür ein.
   - Neue Aktion `furnace_fault` in `opcua/control.py`, `opcua/nodes.py`, `api/schemas.py` und ein Button im Dashboard.
   - Test zuerst: In `tests/test_monitoring.py` prüfen, dass `dev_furnace_c` als einziges Merkmal driftet.
   - Spannende Frage: Ist das ein Sensorfehler oder ein Prozessfehler? (Tipp: Der Ofen heizt wirklich anders, also ändert sich auch die Linse.) Muss die Entscheidungslogik das unterscheiden?

2. **OPC UA mit Verschlüsselung** (mittel, ein Abend)
   - asyncua kann Zertifikate erzeugen und `Basic256Sha256_SignAndEncrypt` nutzen.
   - Server in `opcua/cell.py`, Client in `opcua/collector.py`. Zertifikatspfade in `config.py`.
   - Danach kannst du sagen: „Ich habe die Maschinenanbindung abgesichert.“ Das passt zu IT-Sicherheit in deinem Studium.

3. **Rezeptvorschlag mit Bayes'scher Optimierung** (anspruchsvoll, 2–3 Abende)
   - Ziel: Parameter finden, die PV minimieren und die Dicke halten.
   - Nutze das Hybridmodell als Ersatzfunktion oder den Simulator direkt, z. B. mit scikit-optimize oder BoTorch.
   - Lies vorher Leyendecker et al. (2026) und die Frontiers-Studie zu Bayesian Experimental Design.

## 6. Bevor du es auf GitHub stellst

- [ ] `pytest` läuft bei dir unter Windows grün.
- [ ] `presswatch demo --fresh` läuft, du hast die 5 Schritte aus dem README selbst durchgespielt.
- [ ] Mindestens eine Erweiterung aus Abschnitt 5 eingebaut.
- [ ] Eigenes Git-Repository mit sinnvollen Commits (`git init`, dann in Schritten committen: Simulator, Modelle, Überwachung, API, OPC UA, Dashboard, Doku).
- [ ] Im README oben deinen Namen und einen Satz, warum du das gebaut hast.
- [ ] Repository auf GitHub öffentlich, Link im Anschreiben und im Lebenslauf unter „Projekte“.
