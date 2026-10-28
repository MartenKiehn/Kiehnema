# Linux-Installation und Updates

Kiehnema prüft ungefähr alle sechs Stunden mit zufälligem Zeitversatz auf neue stabile
GitLab-Releases. Administratoren sehen einen Hinweis und installieren unter **Updates**.
Ein Release wird erst nach Tests, Image-Build und Signatur angeboten. Eine Änderung der
Versionsnummer allein veröffentlicht noch kein Update.

Während Download und Vorbereitung bleibt die Anwendung erreichbar. Danach werden neue
Änderungen gesperrt und laufende Jobs zu Ende geführt. Beim eigentlichen Austausch zeigt
ein unabhängiges Gateway eine Wartungsseite mit Status und Ladebalken. Der Browser darf
geschlossen oder neu geladen werden. Nach dem Update melden sich alle Benutzer erneut an.
Automatische Installation ohne Klick ist bewusst nicht enthalten.

## Unterstützte Installation

- Linux auf `amd64` oder `arm64`, rootful Docker Engine mit API 1.45 oder neuer (Engine 26+),
  Docker Compose 2.30+ und Python 3/OpenSSL für den einmaligen Installer.
- Genau ein Kiehnema-Anwendungsprozess pro Datenverzeichnis, lokales Dateisystem mit
  zuverlässigem `flock`, `fsync` und atomarem Umbenennen. Keine NFS-Datenverzeichnisse.
- Host-Swap deaktiviert; genügend RAM für Anwendung und Videoverarbeitung. Compose
  begrenzt die App auf 8 GiB und stellt 4 GiB tmpfs-Arbeitsraum bereit. 16 GiB Host-RAM
  sind ein Ausgangswert, der mit den tatsächlichen Renderjobs zu prüfen ist.
- Unprivilegierte User-Namespaces für Chromiums Sandbox müssen funktionieren. Das
  mitgelieferte Seccomp-Profil, UID 1000, `no-new-privileges` und entfernte Host-Capabilities
  gehören zur Installation. Die Bereitschaftsprüfung startet tatsächlich Chromium und FFmpeg.
- Domain mit HTTPS, eingehende Ports 80/443 für Caddy und ausgehendes HTTPS zu GitLab,
  Registry und den bestehenden Anwendungsdiensten. Die App und der Updater haben keine
  veröffentlichten Verwaltungsports.

Das lokale Entwicklungssetup bleibt nutzbar. Windows/macOS-Server, Rootless Docker,
beliebige bestehende Compose-Dateien und Kubernetes sind keine verwalteten Installationen.
Bestehende Installationen müssen **einmal** übernommen werden; ein bisher nicht vorhandener
Updater kann sich nicht selbst nachinstallieren. Danach benötigen reguläre App-Updates
keinen Terminalzugriff. Betriebssystem, Docker und Host-Konfiguration bleiben Betreiberaufgaben.

## GitLab einmal vorbereiten

1. Container Registry und Generic Package Registry für das Projekt aktivieren. Der Standard
   des Installers ist `https://git.timeglass.de`, Projekt `mkiehn/kiehnema`.
2. `v*`-Tags schützen und nur vertrauenswürdigen Release-Verantwortlichen erlauben.
   Isolierte, für Docker-in-Docker geeignete native Runner ohne Host-Swap mit Tags
   `linux-amd64` und `linux-arm64` bereitstellen; die Testmatrix verwendet beide.
   Für den Mehrplattform-Build braucht dessen BuildKit native Builder für beide Architekturen
   oder funktionierendes QEMU/binfmt. Nicht erst beim produktiven Release einrichten.
3. Den privaten Ed25519-Schlüssel außerhalb des Repositories erzeugen:

   ```bash
   uv run python -m scripts.release keygen \
     --private /sicherer/pfad/release.pem \
     --public /sicherer/pfad/release-keys.json --key-id release-1
   ```

   `KIEHNEMA_SIGNING_KEY` als **geschützte File-CI-Variable** mit dem PEM-Inhalt hinterlegen;
   `KIEHNEMA_SIGNING_KEY_ID=release-1` als geschützte Variable. PEM-Inhalte nicht in Logs
   ausgeben. Den öffentlichen Schlüssel über einen unabhängig vertrauenswürdigen Weg
   an Installationsbetreiber geben. Ein Schlüssel aus demselben ungeprüften Download ist
   kein Vertrauensanker.
4. Für private Auslieferung je Installation einen widerrufbaren Projekt-Zugriffstoken mit
   Reporter-Zugriff, `read_api` und `read_registry` bereitstellen. Die API verwendet
   `PRIVATE-TOKEN`, die Registry den zugehörigen Bot-Benutzernamen. Ein reiner Deploy-Token
   reicht für die Releases-API nicht. Ablaufdatum überwachen; Erneuerung geht unter
   **Updates → Update access**. Die Oberfläche zeigt vorhandene Tokens niemals an.
5. Benötigte Release-Manifeste und Image-Digests von Registry-/Paketbereinigung ausnehmen.
   Das Manifest liegt dauerhaft als Generic Package, nicht als ablaufendes CI-Artefakt.
   Die `update.json`-URL muss ohne Umleitung auf einen anderen Ursprung direkt von GitLab
   abrufbar sein. Bei ausgelagertem Object Storage dessen Download-Proxy passend einrichten.

Die Pipeline testet zunächst und baut danach beide Image-Plattformen. Erst zuletzt wird das
signierte Manifest als Paket und GitLab-Release veröffentlicht. CI-Job-Tokens sind nur für
die Veröffentlichung vorgesehen; sie werden nicht an Installationen verteilt.

## Erste Installation oder Übernahme

Auf einem vorbereiteten Linux-Host brauchst du das signierte `update.json` aus dem Release,
den unabhängig erhaltenen `release-keys.json`, den Installer aus einer vertrauenswürdigen
Quelle und eine private Konfigurationsdatei. Alle nachfolgenden Beispieldateien außerhalb
des Git-Repositories mit Modus 0600 aufbewahren.

`app.env` enthält die benötigte `MG_`-/`AWS_`-Konfiguration. Es ist eine **rohe** `KEY=value`-
Datei ohne Shell-Expansion und ohne Anführungszeichen um Werte. Für unbeaufsichtigtes
Administrator-Setup Benutzername, Passwort und Wiederherstellungsschlüssel über die `MG_ADMIN_*`-Variablen aus
[der Sicherheitsanleitung](security.md#unattended-first-admin-setup) angeben. Ohne diese
fragt der Installer interaktiv. `MG_ADMIN_TOTP_SECRET` ist optional; 2FA kann später im
Adminbereich eingerichtet werden. Der Installer entfernt diese Bootstrap-Werte anschließend
aus seiner Laufzeitkopie. Eigene Originaldateien entsprechend aufbewahren/entfernen.

Optionales `update-credentials.json` für ein privates Projekt:

```json
{"username":"project_BOT_USERNAME","token":"READ_ONLY_PROJECT_TOKEN"}
```

```bash
sudo python3 scripts/install.py \
  --release /root/setup/update.json \
  --keys /root/setup/release-keys.json \
  --app-env /root/setup/app.env \
  --credentials /root/setup/update-credentials.json \
  --origin https://video.example.com
```

Bei einem öffentlichen Projekt `--credentials` weglassen. Mit `--gitlab`, `--project`,
`--directory` und `--name` können die Installationsangaben angepasst werden. Der Installer
überschreibt kein bestehendes Zielverzeichnis. Er prüft die Signatur mit OpenSSL, bevor
er Code aus dem Release ausführt. Bei einem Setupfehler bleibt das Ziel zur Diagnose
erhalten; es wird nicht blind gelöscht oder als erfolgreich erneut installiert.

Für eine bestehende **verschlüsselte** Installation zuerst App und CLI-Arbeit stoppen,
eine unabhängige Sicherung erstellen und `--import-data /pfad/zum/bisherigen/data` ergänzen.
Der Installer kopiert `secure/` unter beiden alten/neuen Koordinator-Sperren; das Original
bleibt bestehen. Bestehende Nutzer und persönliche Recovery-Schlüssel bleiben erhalten.
Alte Klartext-Jobs zuerst über den ausdrücklich dokumentierten
[Legacy-Import](security.md) übernehmen. Das neue Updateverfahren migriert sie nicht heimlich.

Der Installationsordner enthält private Konfiguration, Daten, TLS-Zustand und Updatejournal.
Das `data/secure`-Verzeichnis bleibt außerhalb der Images. Nur der Updater erhält den
Docker-Socket; sein Root-Zugriff ist ein Teil der Vertrauensgrenze. Der Webprozess erhält
lediglich eine lokale, authentifizierte Steuerverbindung.

## Neue Version veröffentlichen

1. Version in `pyproject.toml` auf eine neue stabile `MAJOR.MINOR.PATCH` erhöhen und
   `uv lock` ausführen. Änderungen in `deploy/release-notes.md` beschreiben.
2. `deploy/release.json` prüfen: älteste unterstützte App-/Updater-Version,
   Ausgangs-Datenschemata und Plattformen müssen tatsächlich getestet sein.
3. Tests und Upgradeprobe durchführen, Änderungen committen und pushen.
4. Den passenden geschützten Tag veröffentlichen, beispielsweise `v0.2.0` für `0.2.0`.
   Die Pipeline erzeugt dann Images, Signatur, Paket und GitLab-Release.

Releaseversionen nie wiederverwenden, Tags nicht verschieben und signierte Manifeste nicht
nachträglich verändern. Eine fehlgeschlagene Veröffentlichung mit abweichenden Bytes
erfordert eine neue Version. Das ist keine Oberfläche zum Ausführen beliebiger Shellbefehle:
installierbar sind nur geprüfte, signierte Digests aus dem konfigurierten Repository.

Alte Installationen vergleichen Versionen numerisch und wählen bei Bedarf Zwischenreleases.
Alle drei Komponenten (App, Updater, Gateway) gehören zu einer signierten Version.
Für einen Schlüsselwechsel veröffentlicht der bisherige Schlüssel zunächst eine kompatible
Brückenversion mit `trusted_keys` in `deploy/release.json`, die alten und neuen öffentlichen
Schlüssel enthält. Erst danach mit dem neuen Schlüssel signieren. Brückenversion und deren
Images behalten. Entzug eines kompromittierten Schlüssels erfordert eine gesonderte
Vertrauensentscheidung; normale Schlüsselrotation löst dieses Problem nicht.

Format, Protokoll und Installationslayout stehen momentan auf 1. Unbekannte neuere Formate
werden übersprungen. Änderungen an Layout, benötigten Mounts, Sandbox oder Datenformat
brauchen eine explizite kompatible Brücke und neue Upgrade-/Restore-Tests. Ein bloßes
Hochsetzen der Versionsnummer implementiert keine Datenmigration. Aktuell ist Schema 1
implementiert; verschlüsselte Nutzdaten werden beim Update nicht entschlüsselt.

## Ablauf und Fehlerverhalten

1. Signierte Upgradefolge prüfen, Images vollständig laden und Digest kontrollieren.
2. Neue Änderungen sperren; laufende und wartende Jobs dürfen fertig werden. Nach zwei
   Stunden ohne leere Queue wird der Versuch abgebrochen. Bis dahin ist Abbruch per UI möglich.
3. Wartung einschalten, App stoppen, stabile Datensperre halten und den **gesamten**
   verschlüsselten Speicher samt Kontodatenbank sichern. Kopie mit SHA256 und SQLite prüfen.
4. Migration ohne Netzwerk ausführen. Neue App mit geschlossenem Schreibzugang starten;
   Datenbank, RAM-Arbeitsraum, Chromium und FFmpeg prüfen.
5. Updater und Gateway durch einen unabhängigen Helfer austauschen und deren Healthchecks
   abwarten. Dann installierte Digests/Vertrauensschlüssel dauerhaft speichern und freigeben.

Bei einem Fehler werden Daten, App und Dienste gemeinsam auf den letzten bestätigten
Stand zurückgesetzt. Bei mehreren Zwischenschritten ist das der zuletzt erfolgreich
abgeschlossene Zwischenschritt. Es gibt dazwischen keine Benutzer-Schreibzugriffe.
Ein reines Zurückschalten des App-Images wäre nach Datenänderungen kein sicherer Rollback.

Journal, Sperren und Boot-ID verhindern, dass ein Neustart eine halb migrierte Installation
freigibt. Abgebrochene Transaktionen werden zurückgesetzt; wiederholte Helferabstürze enden
nach drei Starts geschlossen. Wenn auch die Wiederherstellung scheitert, bleibt Wartung
aktiv und die Oberfläche nennt den Hilfebedarf. Die UI verspricht dann keinen Erfolg.

Nach erfolgreichen Updates bleiben die zwei neuesten vollständigen Sicherungen erhalten.
Bei Fehlern werden Sicherungen behalten. Das ersetzt keine unabhängige Sicherung außerhalb
des Servers. Festplattenausfall, fehlender Platz, beschädigte Sicherung oder defekte
Host-Laufzeit können weiterhin Betreiberhilfe verlangen.

## Betrieb und Wiederherstellung

Normale Updates ausschließlich über die Anwendung starten. Für Betreiberkommandos müssen
**beide** Env-Dateien geladen werden; `updates/images.env` enthält die zuletzt bestätigten
Digests, `compose.env` noch die ursprünglichen Werte:

```bash
sudo docker compose --env-file /opt/kiehnema/compose.env \
  --env-file /opt/kiehnema/updates/images.env \
  -f /opt/kiehnema/compose.yaml ps
```

Private Diagnoseinformationen stehen in `updates/job.json`, `catalog.json`, `installed.json`
und `backups/`. Nicht öffentlich hochladen. Der öffentlich abrufbare Status enthält nur Phase,
allgemeinen Text, Fortschritt, Zeit und Reparaturbedarf. Containerlogs sind absichtlich
deaktiviert, damit bestehende Regeln gegen persistente Klartext-Protokolle erhalten bleiben.

Bei `needs_repair=true`: zuerst unabhängige Kopie von `data/` und `updates/` sichern,
Ursache beheben und den gewählten vollständigen Backupstand mit passendem Image wiederherstellen.
Keine Gate-Datei manuell auf `running` setzen und nicht nur den App-Tag ändern. Nach behobenem
Platz-/Laufzeitproblem kann ein Betreiber denselben Runner gezielt erneut versuchen lassen:
unter `command.lock` und `operation.lock` das vorhandene Journal auf `rolling_back` setzen,
`needs_repair` auf false und `runner-attempts.json` entfernen. Alle übrigen Felder, insbesondere
`base`, `specs`, `backup` und `data_changed`, erhalten. Der Supervisor übernimmt den Restore.
Bei fehlendem oder beschädigtem Backup diesen Versuch nicht erzwingen.

## Prüfung vor der ersten Freigabe

```bash
uv run pytest tests/test_updates.py tests/test_update_recovery.py tests/test_update_web.py
docker build --target test -t kiehnema-update-tests .
docker run --rm --cap-drop ALL --security-opt no-new-privileges \
  --security-opt seccomp=deploy/chromium-seccomp.json \
  kiehnema-update-tests python -m pytest -q -o cache_dir=/tmp/pytest-cache
docker build --target runtime -t kiehnema-update-dev:0.1.0 .
docker build --target gateway -t kiehnema-update-gateway .
uv run python -m scripts.test_updates_linux
```

Die letzte Probe erstellt ausschließlich eigene `kmtest-*`-Container, Volumes, Registry und
synthetische Releases und entfernt sie wieder. Sie prüft Update, Dienstwechsel, unabhängige
Wartungsseite, absichtlich fehlgeschlagene Migration, Restore und erneute Kontoentschlüsselung.
Auf Docker Desktop darf **nur für synthetische Testdaten** `--synthetic-no-swap` verwendet werden;
damit wird `/proc/swaps` für die Testcontainer simuliert. Die Produktivprüfung wird nicht verändert.

Vor Freigabe auf echte Installationen zusätzlich beide CPU-Plattformen auf der vorgesehenen
Linux-Hostkonfiguration, Host-Neustart/Stromausfall, privaten GitLab-/Registry-Zugriff,
Signierschlüssel und eine zweite unabhängige Installation abnehmen. Die lokale Dockerprobe
ersetzt diese Infrastrukturabnahme nicht. Live-Bedrock-Aufrufe sind für die Updateprüfungen unnötig.

Referenzen: [GitLab Releases API](https://docs.gitlab.com/api/releases/),
[Generic Packages](https://docs.gitlab.com/user/packages/generic_packages/),
[Compose env_file](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/),
[Docker Mehrplattform-Builds](https://docs.docker.com/build/building/multi-platform/).
