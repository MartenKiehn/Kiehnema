# Kiehnema

**Deine Website. Deine Marke. Dein Video.**

[Deutsch](#deutsch) · [English](#english)

![Kiehnema-Demo / Kiehnema demo](assets/readme/kiehnema-preview.mp4){width=432}

[▶ Demo ansehen / Watch demo — 60 s · MP4 · 1080 × 1350](assets/readme/kiehnema-preview.mp4)

<details>
<summary>Vorschaubild / Preview image</summary>

[![Kiehnema: Von der Website zum Video im Markenlook / From website to on-brand video](assets/readme/kiehnema-preview.jpg)](assets/readme/kiehnema-preview.mp4)

</details>

## Deutsch

Kiehnema macht aus einer Website animierte Social-Media-Videos im passenden Markenlook.
Die Anwendung liest Inhalte, Farben, Schriften und Bilder, entwickelt daraus ein Storyboard
und erstellt eine animierte Vorschau. Du prüfst und bearbeitest das Ergebnis, bevor die
fertigen Videos gerendert werden. Das Video oben zeigt Kiehnema selbst als Beispiel.

### Von der URL zum fertigen Video

1. **Website und Briefing eingeben.** URL, Videolänge und kreative Richtung festlegen.
2. **Marke und Geschichte entwickeln.** Kiehnema analysiert die Website und erzeugt Markenübersicht, Storyboard und Animation.
3. **Vorschau gestalten.** Beide Formate ansehen, Szenentexte, Reihenfolge und Dauer ändern, Kommentare setzen oder visuelle Ideen mit Texten und Bildern ergänzen.
4. **Freigeben und herunterladen.** Mit **Approve & render** entstehen beide MP4s inklusive Musik. **Continue editing** öffnet das Video später erneut zur Bearbeitung.

| Ausgabe | Auflösung | Geeignet für |
|---|---|---|
| Feed · 4:5 | 1080 × 1350 | LinkedIn- und Instagram-Feed |
| Story · 9:16 | 1080 × 1920 | Reels und Stories |

Beide Formate werden mit 30 fps, H.264 und AAC-Stereoton ausgegeben. Die Oberfläche ist auf Englisch.

### Lokal starten

Du brauchst Python 3.13+, `uv`, Node.js/npm und AWS-Zugang mit Zugriff auf das konfigurierte
Bedrock-Modell. Modellaufrufe verursachen Kosten in deinem AWS-Konto.

```bash
uv sync
npm install
uv run playwright install chromium
cp .env.example .env
# AWS-Profil, Region und Modell in .env an deinen Zugang anpassen.
uv run python -m app
```

Öffne anschließend **http://127.0.0.1:8000**. Die Einstellungen stehen in
[`.env.example`](.env.example); AWS-Anmeldedaten werden über die übliche AWS-Konfiguration bereitgestellt.

Lass die Anwendung während der Verarbeitung laufen. Jobs werden nacheinander ausgeführt;
ein Neuladen der Seite unterbricht sie nicht. Nach einem Neustart bleiben abgeschlossene
Jobs und gespeicherte Vorschauen erhalten. Unterbrochene Arbeit startet nicht automatisch neu.

### Daten und Betriebsmodell

Der Stand auf `main` ist eine lokale Einzelbenutzer-Anwendung. Betreibe sie auf dem eigenen
Rechner und mache sie nicht öffentlich zugänglich. Nutzerverwaltung und verschlüsselte
Arbeitsbereiche sind noch nicht Bestandteil dieses Branches.

Jobs, Website-Inhalte und Renderings liegen lokal in `data/` beziehungsweise `out/` und sind
von Git ausgeschlossen. Für die KI-Verarbeitung werden ausgewählte Inhalte an AWS Bedrock
übermittelt. Die Demo oben ist bewusst als separate Kopie unter [`assets/readme/`](assets/readme/)
versioniert und bleibt auch beim Löschen des ursprünglichen Jobs erhalten.

### Technik und Weiterentwicklung

Python, FastAPI und HTMX bilden die Anwendung; LangChain/LangGraph steuern die Generierung
über AWS Bedrock. HTML/CSS und GSAP liefern die Animation, Playwright und FFmpeg die Videos.

```bash
uv run pytest                                   # ohne Live-Bedrock-Aufrufe
uv run pytest tests/test_web.py -m browser -q    # UI-Prüfungen mit Chromium
```

- [Bedienung, CLI und Entwicklung](docs/usage.md)
- [Weboberfläche und Review-Ablauf](docs/phase4-ui.md)
- [Kompositionsvertrag](docs/harness-contract.md) und [Pipeline](docs/phase3-pipeline.md)
- [Ursprüngliche Demo: they consulting](docs/original-demo.md)
- [Projektplan](docs/PLAN.md) und [Beitragsrichtlinien](AGENTS.md)

---

## English

**Your website. Your brand. Your video.**

Kiehnema turns a website into animated social videos that match its brand.
The app reads content, colors, fonts and images, develops a storyboard and creates an
animated preview. You review and edit the result before the final videos are rendered.
The video above uses Kiehnema itself as the example.

### From URL to finished video

1. **Enter a website and brief.** Set the URL, video length and creative direction.
2. **Develop the brand and story.** Kiehnema analyzes the website and generates a brand overview, storyboard and animation.
3. **Shape the preview.** Watch both formats, change scene copy, order and duration, add comments or contribute visual ideas using text and images.
4. **Approve and download.** **Approve & render** produces both MP4s with music. **Continue editing** lets you return to the video later.

| Output | Resolution | Designed for |
|---|---|---|
| Feed · 4:5 | 1080 × 1350 | LinkedIn and Instagram feeds |
| Story · 9:16 | 1080 × 1920 | Reels and Stories |

Both formats export at 30 fps with H.264 video and AAC stereo audio. The interface is in English.

### Run locally

You need Python 3.13+, `uv`, Node.js/npm and AWS credentials with access to the configured
Bedrock model. Model calls incur charges in your AWS account.

```bash
uv sync
npm install
uv run playwright install chromium
cp .env.example .env
# Adjust the AWS profile, region and model in .env for your account.
uv run python -m app
```

Then open **http://127.0.0.1:8000**. Settings are listed in
[`.env.example`](.env.example); AWS credentials use the standard AWS configuration.

Keep the app running while it processes jobs. Jobs run sequentially; reloading the page
does not interrupt them. Completed jobs and saved previews survive an app restart.
Interrupted work does not restart automatically.

### Data and deployment

The version on `main` is a local, single-user app. Run it on your own machine and do not
expose it publicly. User management and encrypted workspaces are not yet part of this branch.

Jobs, website content and renders are stored locally in `data/` or `out/` and excluded from Git.
Selected content is sent to AWS Bedrock for AI processing. The demo above is deliberately
versioned as a separate copy in [`assets/readme/`](assets/readme/), so it remains available
even if the original job is deleted.

### Technology and development

Python, FastAPI and HTMX power the app; LangChain/LangGraph orchestrate generation through
AWS Bedrock. HTML/CSS and GSAP provide the animation, while Playwright and FFmpeg produce the videos.

```bash
uv run pytest                                   # no live Bedrock calls
uv run pytest tests/test_web.py -m browser -q    # Chromium UI checks
```

- [Usage, CLI and development](docs/usage.md)
- [Web interface and review workflow](docs/phase4-ui.md)
- [Composition contract](docs/harness-contract.md) and [pipeline](docs/phase3-pipeline.md)
- [Original demo: they consulting](docs/original-demo.md)
- [Project plan](docs/PLAN.md) and [contributor guidelines](AGENTS.md)
