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

1. **Website und Briefing eingeben.** Öffentliche Website-URL, Videolänge und kreative Richtung festlegen.
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

Richte vor der ersten Generierung ein privates **RAM-Arbeitsverzeichnis** ein und setze
`MG_WORK_DIR` entsprechend. Unter Linux wird tmpfs ohne Swap benötigt, unter macOS eine
RAM-Disk mit verschlüsseltem Swap. Gewöhnliche Festplattenverzeichnisse werden abgelehnt.
Die Schritte für beide Systeme stehen in der [Einrichtungsanleitung](docs/security.md).

```bash
uv sync
npm install
uv run playwright install chromium
cp .env.example .env
# AWS-Profil, Region, Modell und MG_WORK_DIR in .env anpassen.
uv run python -m app
```

Beim ersten Start legst du im Terminal das Administratorkonto an: Benutzername, Passwort
mit 15–256 Zeichen und ein Authenticator-Code (TOTP). Bewahre den persönlichen
Wiederherstellungsschlüssel außerhalb des Servers auf. Über **Users** lädt der Administrator
weitere Nutzer ein; jedes Konto erhält ein eigenes Passwort und einen eigenen Authenticator.
Für automatische Deployments kannst du das erste Administratorkonto über die vier
`MG_ADMIN_*`-Variablen vorgeben; siehe [Setup ohne Terminal](docs/security.md#unattended-first-admin-setup).

Öffne anschließend **http://127.0.0.1:8000** und melde dich an. Die Einstellungen stehen in
[`.env.example`](.env.example); AWS-Anmeldedaten werden über die übliche AWS-Konfiguration bereitgestellt.

Lass die Anwendung während der Verarbeitung laufen. Jobs werden nacheinander ausgeführt;
ein Neuladen der Seite oder Abmelden unterbricht bereits eingereichte Jobs nicht. Nach einem
Neustart bleiben abgeschlossene Jobs und gespeicherte Vorschauen erhalten und werden nach
der Anmeldung wieder zugänglich. Unterbrochene Arbeit startet nicht automatisch neu.

### Daten und Betriebsmodell

Jedes Konto sieht seine eigenen Jobs und Videos. Auch der Anwendungsadministrator kann
keine fremden Job-Inhalte öffnen. Inhalte, Dateinamen und Pipeline-Zwischenstände werden
unter `data/secure/` mit getrennten Nutzerschlüsseln verschlüsselt gespeichert. Kontonamen,
Zuordnungen, Dateigrößen und Aktivität bleiben als Metadaten sichtbar.

Die Verarbeitung erfolgt im RAM; für die KI-Verarbeitung werden ausgewählte Inhalte an
AWS Bedrock übermittelt. Die Verschlüsselung schützt gespeicherte Inhalte, bietet aber
keinen Schutz vor Root-Zugriff oder kompromittierten Prozessen während der Verarbeitung.
Für den Serverbetrieb sind HTTPS, ein vertrauenswürdiger Reverse-Proxy und ein eigener
Betriebsnutzer nötig; pro Datenverzeichnis läuft genau ein Anwendungsprozess.

**Bestehende Jobs werden nicht automatisch migriert.** Der explizite Import kopiert sie
in den verschlüsselten Speicher, entfernt aber keine unverschlüsselten Originale oder
Backups. Einrichtung, Wiederherstellung und Migration erklärt die [Sicherheitsanleitung](docs/security.md).

Jobdaten sind von Git ausgeschlossen. Downloads und eigenständige Demo-, Crawl-, Render-
und Screenshot-Befehle erzeugen unverschlüsselte Exporte. Die Demo oben ist bewusst als
separate Kopie unter [`assets/readme/`](assets/readme/) versioniert und bleibt auch beim
Löschen des ursprünglichen Jobs erhalten.

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

1. **Enter a website and brief.** Set a public website URL, video length and creative direction.
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

Before generating your first video, configure a private **RAM workspace** and set
`MG_WORK_DIR` accordingly. Linux requires tmpfs with swap disabled; macOS requires a
RAM disk with encrypted swap. Ordinary disk directories are rejected.
The [setup guide](docs/security.md) covers both systems.

```bash
uv sync
npm install
uv run playwright install chromium
cp .env.example .env
# Adjust the AWS profile, region, model and MG_WORK_DIR in .env.
uv run python -m app
```

On the first start, create the administrator account in the terminal: choose a username,
a password of 15–256 characters and an authenticator code (TOTP). Keep the personal recovery
key outside the server. The administrator invites additional users through **Users**;
each account gets its own password and authenticator.
For automated deployments, supply the first administrator through the four `MG_ADMIN_*`
variables; see [unattended setup](docs/security.md#unattended-first-admin-setup).

Then open **http://127.0.0.1:8000** and sign in. Settings are listed in
[`.env.example`](.env.example); AWS credentials use the standard AWS configuration.

Keep the app running while it processes jobs. Jobs run sequentially; reloading the page
or signing out does not interrupt already submitted jobs. Completed jobs and saved previews
survive an app restart and become accessible again after signing in.
Interrupted work does not restart automatically.

### Data and deployment

Each account sees its own jobs and videos. Even the application administrator cannot open
another user's job content. Contents, filenames and pipeline checkpoints are encrypted
under `data/secure/` with separate user keys. Account names, ownership, file sizes and
activity remain visible as metadata.

Processing takes place in RAM; selected content is sent to AWS Bedrock for AI processing.
Encryption protects stored content but does not protect against root access or compromised
processes during processing. Server deployment requires HTTPS, a trusted reverse proxy and
a dedicated service account, with exactly one application process per data directory.

**Existing jobs are not migrated automatically.** The explicit importer copies them into
encrypted storage but does not remove plaintext originals or backups. See the
[security guide](docs/security.md) for setup, recovery and migration.

Job data is excluded from Git. Downloads and standalone demo, crawl, render and screenshot
commands produce plaintext exports. The demo above is deliberately versioned as a separate
copy in [`assets/readme/`](assets/readme/), so it remains available even if the original job
is deleted.

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
