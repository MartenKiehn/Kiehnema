# Original demo: they consulting (30 s)

[Zur Projektübersicht / Back to overview](../README.md)

Social-Media-Spot für LinkedIn & Instagram, gebaut aus Inhalten, Farben, Schriften und Logo von
[they-consulting.de](https://www.they-consulting.de/).

## Ergebnis

| Datei | Format | Einsatz |
|---|---|---|
| `out/they-consulting-feed-1080x1350.mp4` | 4:5 | LinkedIn-Feed, Instagram-Feed |
| `out/they-consulting-story-1080x1920.mp4` | 9:16 | Instagram Reels/Stories, LinkedIn mobil |

30 fps, H.264 (BT.709, korrekt getaggt), AAC-Stereo mit eigens erzeugter Musik.
Diese Ausgabedateien sind lokal und werden mit `npm run render` erzeugt; sie gehören nicht ins Git-Repo.

## Storyboard

| Zeit | Szene | Inhalt (Quelle) |
|---|---|---|
| 0,0–5,5 s | Hook | „Noch ein KI-Prototyp für die Schublade?“ – ein Prototyp sinkt in die Schublade „Prototypen“, wird herausgezogen, die Schublade knallt zu, „Schublade?“ wird gestrichen → „Nicht bei uns. Wir entwickeln KI, die im Tagesgeschäft wirkt.“ – die Karte wird zum Live-Dashboard (Startseite: „keine Prototypen, die in der Schublade landen“) |
| 5,5–9,0 s | Claim | heller Grund, Logo setzt sich in Originalfarben zusammen → „they make AI work.“ |
| 9,0–14,5 s | Vorgehen | 5 Phasen: KI Kompass → Discovery Workshop → Opportunity Sprint → Implementierung → Rollout & Enablement, 12 Wochen |
| 14,5–19,0 s | Budgetgarantie | Zitat Lars Quell: „Was wir sagen, gilt. Was wir kalkulieren, bleibt. Was wir starten, bringen wir zu Ende.“ |
| 19,0–22,5 s | Souveränität | TISAX-konform · Hosting in Europa · DSGVO-ready · KI ohne Datenweitergabe |
| 22,5–25,5 s | Kunden | VW, DB, Bentley, Cariad, LHIND, Contipark, EJF, VKU, Bankenverband |
| 25,5–30,0 s | Outro | Farb-Wipe, Logo, CTA „Erstgespräch vereinbaren“, they-consulting.de |

Lesbarkeit: Jeder Text ist nach spätestens ~1 s komplett eingeblendet und steht dann mindestens ~1,5–2 s ruhig.

## Musik

`music.mjs` synthetisiert den Track komplett im Code (keine Samples → keine Lizenzfragen):
120 BPM, Am–F–C–G. Im Hook: Ticken, Schubladen-Geräusch, Rums beim Zuknallen, danach öffnet sich die Musik
mit Riser bis zum Drop + Impact genau auf dem Logo (5,5 s). Whooshes auf
jedem Szenenwechsel, Glockentöne beim Zusammensetzen des Logos, Auflösung nach C im Outro.
Die Zeitpunkte kommen aus `plan.js`, Ton und Bild bleiben also synchron, wenn sich das Timing ändert.

## Bearbeiten & neu rendern

```bash
npm install
npx playwright install chromium   # einmalig
npm run preview                   # http://127.0.0.1:5173  (Leertaste = Play, ?format=story für 9:16; ohne Ton)
npm run render                    # Musik erzeugen + beide Formate nach out/
node render.mjs --format=feed --stills=5,12.5   # einzelne Standbilder zur Kontrolle
```

- Szenen-Timing (Bild **und** Ton): `plan.js`
- Texte und Szenenaufbau: `index.html`
- Farben, Größen, Layout: `style.css` (9:16-Anpassungen unter `.fmt-story`)
- Animationen: `main.js`
- Musik, Sounddesign, Lautheit: `music.mjs`

Technik: HTML/CSS + [GSAP](https://gsap.com) (kostenlos, auch kommerziell). Playwright rendert jedes Frame
deterministisch, ffmpeg (`ffmpeg-static`) kodiert das MP4.
