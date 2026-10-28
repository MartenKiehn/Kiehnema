# Kiehnema

**Your website. Your brand. Your video.**

[English](#english) · [Deutsch](#deutsch)

![Kiehnema demo / Kiehnema-Demo](assets/readme/kiehnema-preview.mp4){width=432}

[▶ Watch demo / Demo ansehen — 60 s · MP4 · 1080 × 1350](assets/readme/kiehnema-preview.mp4)

<details>
<summary>Preview image / Vorschaubild</summary>

[![Kiehnema: From website to on-brand video / Von der Website zum Video im Markenlook](assets/readme/kiehnema-preview.jpg)](assets/readme/kiehnema-preview.mp4)

</details>

## English

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

### Install on a server

Published GitHub releases include an interactive installer for a fresh, dedicated
**Ubuntu 24.04 instance (amd64/arm64)**:

```bash
curl -fsSL --proto '=https' https://github.com/MartenKiehn/Kiehnema/releases/latest/download/install.sh -o kiehnema-install.sh && sudo bash ./kiehnema-install.sh
```

Prepare DNS, the security group and an EC2 IAM role first; allow at least 16 GiB RAM and
30 GiB free disk space. The installer asks for the domain, AWS region and Bedrock model,
prepares Docker and the host, and walks through administrator creation. See the
[server installation workflow](docs/updates.md#serverinstallation-mit-einem-befehl) for prerequisites
and host changes. This URL becomes available after the first signed release containing `install.sh`.

### Run locally

Linux servers can use the [managed installation with updates through the UI](docs/updates.md).
It checks GitHub releases regularly, shows installation progress, and restores the previous
working version when an update fails.

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
a password of 15–256 characters. Two-factor authentication is initially optional. Keep the personal recovery
key outside the server. The administrator invites additional users through **Users**;
each account gets its own password. Administrators can enable or disable two-factor
authentication per user or require it for everyone, including administrators and future
users. Affected users set up their own authenticator before accessing their workspace.
For automated deployments, supply the first administrator through the `MG_ADMIN_*`
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

### Push to GitLab and GitHub

CI runs exclusively in **GitHub → Actions**: tests and container update probes on AMD64
and ARM64 for pushes and pull requests. GitLab pipelines are disabled; existing queued
jobs can be cancelled there. Release tags publish GitHub Releases and GHCR images after successful tests. GitLab is only a code backup.
Required GitHub secrets and variables are documented in the
[release setup guide](docs/updates.md#github-einmal-vorbereiten).

After committing, push the current branch to
`git@git.timeglass.de:mkiehn/kiehnema.git` and
`git@github.com:MartenKiehn/Kiehnema.git`:

```bash
python3 scripts/push.py
```

SSH write access to both repositories is required. The script uses fixed destination URLs;
no additional Git remotes are needed. It does not create commits or push uncommitted changes,
other branches or tags. Check out each additional branch and run the script again to publish
it. Release tags are pushed separately.

Both destinations are attempted without force-pushing. Any failed push produces a nonzero
exit status; the other repository may already be updated. Resolve the error and rerun.
Changes and merges made directly on GitLab or GitHub are not synchronized automatically:
update the affected local branch first, then run the script. Pushing to a public GitHub
repository publishes the commits and their history.

- [Usage, CLI and development](docs/usage.md)
- [Web interface and review workflow](docs/phase4-ui.md)
- [Composition contract](docs/harness-contract.md) and [pipeline](docs/phase3-pipeline.md)
- [Original demo: they consulting](docs/original-demo.md)
- [Project plan](docs/PLAN.md) and [contributor guidelines](AGENTS.md)

---

## Deutsch

**Deine Website. Deine Marke. Dein Video.**

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

### Auf einem Server installieren

Für eine frische, dedizierte **Ubuntu-24.04-Instanz (amd64/arm64)** gibt es einen interaktiven
Installer als GitHub-Release-Anhang. Nach dem ersten entsprechend veröffentlichten Release:

```bash
curl -fsSL --proto '=https' https://github.com/MartenKiehn/Kiehnema/releases/latest/download/install.sh -o kiehnema-install.sh && sudo bash ./kiehnema-install.sh
```

Vorher Domain, Security Group und EC2-IAM-Rolle vorbereiten; mindestens 16 GiB RAM und
30 GiB freien Speicher einplanen. Das Skript fragt Domain, AWS-Region und Bedrock-Modell ab,
bereitet Docker und den Host vor und führt durch die Admin-Einrichtung. Details und die
Host-Änderungen stehen im [Server-Installationsablauf](docs/updates.md#serverinstallation-mit-einem-befehl).
Der Download steht erst bereit, wenn ein signierter Release mit `install.sh` veröffentlicht wurde.

### Lokal starten

Für Linux-Server gibt es eine [verwaltete Installation mit Updates über die Oberfläche](docs/updates.md).
Sie prüft GitHub-Releases regelmäßig, zeigt während der Installation den Fortschritt und
stellt bei fehlgeschlagenen Updates den vorherigen Stand wieder her.

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
mit 15–256 Zeichen. Zwei-Faktor-Authentifizierung ist zunächst optional. Bewahre den persönlichen
Wiederherstellungsschlüssel außerhalb des Servers auf. Über **Users** lädt der Administrator
weitere Nutzer ein und aktiviert oder deaktiviert 2FA pro Nutzer. Ein zusätzlicher Schalter
verlangt 2FA für alle, einschließlich Administratoren und neuer Nutzer. Betroffene Nutzer
richten ihren eigenen Authenticator ein, bevor sie weiterarbeiten können.
Für automatische Deployments kannst du das erste Administratorkonto über die
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

### Nach GitLab und GitHub pushen

CI läuft ausschließlich unter **GitHub → Actions**: Tests und Container-Updateproben
auf AMD64 und ARM64 bei Pushes und Pull Requests. GitLab-Pipelines sind deaktiviert;
bereits wartende Jobs können dort abgebrochen werden. Release-Tags starten nach erfolgreichen
Tests die Veröffentlichung als GitHub Release mit Images in GHCR. GitLab dient nur als Code-Sicherung. Die dafür benötigten GitHub-Secrets und Variablen
stehen in der [Release-Einrichtung](docs/updates.md#github-einmal-vorbereiten).

Nach dem Commit pusht dieses Skript den aktuellen Branch nach
`git@git.timeglass.de:mkiehn/kiehnema.git` und
`git@github.com:MartenKiehn/Kiehnema.git`:

```bash
python3 scripts/push.py
```

Voraussetzung ist SSH-Schreibzugriff auf beide Repositories. Das Skript verwendet feste
Zieladressen; zusätzliche Git-Remotes sind nicht nötig. Es erstellt keine Commits und
überträgt keine uncommitteten Änderungen, anderen Branches oder Tags. Für jeden weiteren
Branch diesen auschecken und das Skript erneut ausführen. Release-Tags werden separat gepusht.

Beide Ziele werden ohne Force-Push versucht. Schlägt ein Push fehl, endet das Skript mit
Fehlerstatus; das andere Repository kann bereits aktualisiert sein. Nach Beheben der Ursache
erneut ausführen. Änderungen und Merges direkt auf GitLab oder GitHub werden nicht automatisch
synchronisiert: den betroffenen Branch zuerst lokal aktualisieren und dann das Skript starten.
Bei einem öffentlichen GitHub-Ziel werden die gepushten Commits samt ihrer Historie öffentlich.

- [Bedienung, CLI und Entwicklung](docs/usage.md)
- [Weboberfläche und Review-Ablauf](docs/phase4-ui.md)
- [Kompositionsvertrag](docs/harness-contract.md) und [Pipeline](docs/phase3-pipeline.md)
- [Ursprüngliche Demo: they consulting](docs/original-demo.md)
- [Projektplan](docs/PLAN.md) und [Beitragsrichtlinien](AGENTS.md)
