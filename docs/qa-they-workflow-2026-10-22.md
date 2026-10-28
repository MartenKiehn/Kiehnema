# Workflow-Test: they consulting · 22.10.2026

Status: läuft. Nur tatsächlich beobachtete Ergebnisse werden als geprüft markiert.

## Testaufbau

- Lokale App: http://127.0.0.1:8000, Testkonto mit Adminrechten, ohne 2FA.
- Bedienung und visuelle Kontrolle: Safari.
- Quelle: https://www.they-consulting.de/ (in Safari geöffnet).
- Ziel: 30 Sekunden auf Deutsch, 4:5 und 9:16, Entscheider im Mittelstand, KI von der Strategie bis zum produktiven Betrieb, CTA „Erstgespräch vereinbaren“.
- Website-Scan: Standardwerte; Kundenlogos aus.
- Geplant: Erstellen → Fortschritt → Marken-/Inhaltsprüfung → beide Vorschauen → Szenen-/Kommentar-/Frame-Bearbeitung → Revision → Freigabe/Rendering → Downloads → erneutes Bearbeiten.
- Keine Veröffentlichung oder Kontaktaufnahme mit der Quellwebsite. Modellaufrufe dieses Live-Tests können AWS-Kosten verursachen.

## Beobachtungen und Verbesserungsideen

| ID | Priorität | Beobachtung / Reproduktion | Auswirkung | Verbesserung |
| --- | --- | --- | --- | --- |
| F01 | Hoch | Bei fehlender RAM-Disk lässt die Startseite das Formular normal abschicken. Erst der angelegte Job scheitert mit `ValueError: MG_WORK_DIR must point to a mounted RAM disk on macOS.` | Ein Nutzer investiert Zeit ins Briefing, obwohl die Umgebung nicht betriebsbereit ist. Die technische Fehlermeldung erklärt den nächsten Schritt nicht. | Bereits vor „Create preview“ Betriebsbereitschaft prüfen; verständliche Meldung mit einem Admin-Hinweis zur RAM-Disk anzeigen. |
| F02 | Mittel | Ein gescheiterter Job bietet nur „New video“ und „Delete this job and its files“. Kein erneuter Versuch mit dem bestehenden Briefing. | Nach Beheben eines Umgebungsfehlers muss das Briefing erneut eingegeben werden. | „Erneut versuchen“ oder „Als neues Video übernehmen“ mit URL, Text und Scan-Einstellungen anbieten; kostenpflichtige Wiederholung klar kennzeichnen. |
| F03 | Mittel | Der Standard-Scan verbraucht einen Teil seines 20-Seiten-Limits für Impressum/Datenschutz, Code of Conduct und Karriere. | Bei begrenztem Scanbudget bleibt weniger Raum für relevante Leistungen und Projektbeispiele. Die Auswirkung auf diesen fertigen Film ist noch offen. | Leistungs-, Angebots- und Projektseiten priorisieren; rechtliche Seiten standardmäßig niedriger gewichten und gelesene Seiten auswählbar machen. |
| F04 | Niedrig | In der Jobliste lautet der Status „Compose“, auf der Detailseite dagegen „Bringing your story to life“. Zwei Jobs derselben Website sind nur anhand ihrer technischen ID und des Status unterscheidbar. | Fachbegriffe und IDs machen das Wiederfinden verschiedener Versuche unnötig schwer. | Einheitliche verständliche Statusnamen sowie automatisch vorgeschlagene oder editierbare Videotitel und Erstellzeit verwenden. |
| F05 | Mittel | Die lang laufende Animationsphase nennt „around 11 minutes“, bietet aber keinen Abbruch und zeigt keine verstrichene Zeit der Phase. | Ein versehentlich gestarteter oder unerwünschter Auftrag lässt sich in der Oberfläche nicht stoppen; längere stille Modellphasen wirken schwer einschätzbar. | Sicheren Abbruch mit Erhalt der Zwischenstände ergänzen; Startzeit, verstrichene Zeit und letzte Aktivität anzeigen. Die bereits vorhandene Erklärung zur verzögerten Kostenanzeige beibehalten. |

## Durchgeführte Schritte

1. Quellwebsite in Safari gelesen: KI-/Cloud-Beratung, petrol-/türkisfarbene Marke mit gelben Akzenten; Zielgruppe und Angebote sind auf der Website erkennbar.
2. Startformular mit URL und konkretem deutschem Briefing abgeschickt. Standarddauer 30 s beibehalten.
3. Job `c05dba7d0a14305957d3296baadd1b58` scheiterte vor Modellaufrufen; Anzeige `$0.00 estimated`. Fehlende RAM-Disk als Umgebungsursache bestätigt.
4. Neues, ausschließlich temporäres 4-GiB-RAM-Gerät erstellt und unter `/Volumes/KiehnemaRAM` eingebunden. Vorhandene Sicherheitsprüfung bestätigt `/Volumes/KiehnemaRAM/work`; keine Schutzprüfung deaktiviert.
5. Neues Briefing mit identischen Vorgaben eingereicht: Job `5d55500420f7e25ac0fb8d7a41af240d`. Scan läuft; Seite 7 von maximal 20 erreicht. Erweiterte Scan-Einstellungen zeigen Grenzen und Auswirkungen auf Zeit/Kosten verständlich an.
6. Aktivitätsprotokoll geöffnet. Aktuelle gelesene URL und bisherige Seiten sind sichtbar; die nächste Produktionsphase wird erklärt. Positiv: Kosten werden ausdrücklich als Schätzung und erst nach abgeschlossenen Modellaufrufen ausgewiesen.
7. Scan hat 20 Seiten abgeschlossen. Während der Markenextraktion erscheinen echte Textauszüge mit Links zur jeweiligen Quelle; weitere Auszüge sind ausklappbar. Das geöffnete Aktivitätsprotokoll bleibt beim Wechsel der Phase geöffnet.
8. Fakten-/Storyboard-Erstellung abgeschlossen: 29 laut App verifizierte Fakten, 7 geplante Szenen, geschätzte Modellkosten $0.40. Erkannt werden Weiß, dunkles Blau/Petrol, Gelb und Türkis, passend zur Quellwebsite. Die vollständige inhaltliche Faktenprüfung steht noch aus.
9. Während „Animation“ zur Jobliste navigiert, abgemeldet, ohne 2FA wieder angemeldet und dieselbe Job-URL geöffnet. Job wird weiter verarbeitet; kein erneuter Start und keine erneute Briefing-Eingabe nötig. Geschätzte Animationsdauer laut UI rund 11 Minuten.
10. Alle sieben geplanten Szenen aufgeklappt. Aussagen zu einem MVP in vier Wochen, Betrieb auf EU-Servern und langfristiger Begleitung sind auf der Quellwebsite tatsächlich vorhanden. Als redaktioneller Prüfpunkt: Die ersten beiden Szenen wiederholen dasselbe Schubladen-/Prototypenmotiv; im späteren Review auf unnötige Redundanz achten.

## Grenzen

Noch keine Aussage zur Generierungsqualität, Revision oder zum Export. Keine gemessenen Core Web Vitals, vollständige Netzwerkanalyse, axe-Prüfung oder automatisierte visuelle Baseline. Responsive Darstellung und Tastaturbedienung stehen noch aus.
