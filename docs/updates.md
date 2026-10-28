# Linux-Installation und Updates

Kiehnema prüft ungefähr alle sechs Stunden mit zufälligem Zeitversatz auf neue stabile
GitHub-Releases. Administratoren sehen einen Hinweis und installieren unter **Updates**.
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
- Domain mit HTTPS, eingehende Ports 80/443 für Caddy und ausgehendes HTTPS zu GitHub,
  Registry und den bestehenden Anwendungsdiensten. Die App und der Updater haben keine
  veröffentlichten Verwaltungsports.

Das lokale Entwicklungssetup bleibt nutzbar. Windows/macOS-Server, Rootless Docker,
beliebige bestehende Compose-Dateien und Kubernetes sind keine verwalteten Installationen.
Bestehende Installationen müssen **einmal** übernommen werden; ein bisher nicht vorhandener
Updater kann sich nicht selbst nachinstallieren. Danach benötigen reguläre App-Updates
keinen Terminalzugriff. Betriebssystem, Docker und Host-Konfiguration bleiben Betreiberaufgaben.

## GitHub einmal vorbereiten

GitHub ist die Hauptplattform: Tests, Builds und Veröffentlichung laufen in GitHub Actions
(`.github/workflows/ci.yml`), signierte Manifeste liegen als GitHub-Release-Anhang und Images
in der GitHub Container Registry (`ghcr.io`). GitLab ist ausschließlich eine zweite Kopie
des Git-Repositories. `.gitlab-ci.yml` verhindert neue Pipelines; alte wartende Jobs können
abgebrochen werden. GitLab-Runner, GitLab-Registry und GitLab-Zugangstokens sind nicht nötig.

1. GitHub Actions für `MartenKiehn/Kiehnema` aktivieren. Der Workflow startet bei Pushes
   auf `main`, bei Release-Tags (`v*`) und manuell über **Actions → CI → Run workflow** auf
   `main`, nicht bei Feature-Branch-Pushes oder Pull Requests.
   Eine gemeinsame Concurrency-Gruppe lässt nur einen Workflow-Lauf aktiv: Ein neuer Lauf
   bricht den bisherigen ab, auch bei Release-Tags. Die beiden Architektur-Jobs innerhalb
   dieses Laufs dürfen parallel arbeiten. Während einer gewünschten Veröffentlichung keine
   weiteren Pushes auslösen; nach einem Abbruch gegebenenfalls den Releaseentwurf prüfen.
   Tests laufen nativ auf
   `ubuntu-24.04` (amd64) und `ubuntu-24.04-arm` (arm64). Der Workflow deaktiviert Swap
   auf diesen temporären VMs und erlaubt Chromiums User-Namespace-Sandbox. Für den
   Mehrplattform-Build richtet er QEMU und Buildx ein.
2. Aktive Regeln für `main` und `v*`-Tags einrichten. Nur vertrauenswürdige Verantwortliche
   sollen Code veröffentlichen dürfen; Änderungen/Löschung veröffentlichter Tags verhindern.
   Die Regeln müssen den vorgesehenen direkten Release-Commit auf `main` und die Erstellung
   eines neuen Tags durch den Workflow erlauben. Der Workflow umgeht keine Regeln und
   pusht niemals mit Force. Bei widersprechenden Regeln schlägt die Vorbereitung fehl.
   Der Releasejob verlangt `GITHUB_REF_PROTECTED=true` für die auslösende Referenz:
   `main` beim manuellen Start, den Tag beim Tag-Push. Er prüft, dass der Release-Tag auf
   den getesteten Commit zeigt; beim manuellen Start muss dieser weiterhin auf `main` liegen.
   Das Environment **release** anlegen und Deployments von `main` sowie `v*`-Tags erlauben.
   Falls der Tarif es unterstützt, eine Freigabe durch einen Release-Verantwortlichen
   konfigurieren. Vorbereitung und Veröffentlichung verwenden beide dieses Environment.
   Für private Repositories benötigen Rulesets und Environments GitHub Pro/Team/Enterprise;
   zusätzliche Reviewer-Regeln sind tarifabhängig. Ein manuell startbarer Workflow umgeht
   diese Voraussetzungen nicht. Siehe [Rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
   und [Environments](https://docs.github.com/en/rest/deployments/environments).
3. Den privaten Ed25519-Schlüssel außerhalb des Repositories erzeugen:

   ```bash
   uv run python -m scripts.release keygen \
     --private /sicherer/pfad/release.pem \
     --public /sicherer/pfad/release-keys.json --key-id release-1
   ```

   Im GitHub-Environment **release** unter Secrets den vollständigen PEM-Inhalt als
   `KIEHNEMA_SIGNING_KEY_PEM` hinterlegen. Unter Variables `KIEHNEMA_SIGNING_KEY_ID=release-1`
   setzen. Der Workflow schreibt den Schlüssel nur für die Signierung in eine private
   temporäre Datei und entfernt sie anschließend. Den öffentlichen Schlüssel unabhängig
   vertrauenswürdig an Installationsbetreiber verteilen und den privaten Schlüssel sichern.
4. Der Releasejob verwendet GitHubs automatisch erzeugtes, zeitlich begrenztes `GITHUB_TOKEN`
   mit `contents: write` und `packages: write`. Dafür keinen persönlichen Token hinterlegen.
   Normale Testjobs haben nur Lesezugriff auf das Repository und keine Release-Secrets.
5. Die erzeugten Packages `ghcr.io/martenkiehn/kiehnema` und
   `ghcr.io/martenkiehn/kiehnema/gateway` mit dem Repository verknüpft lassen. Für anonyme
   Installation müssen sowohl das Repository als auch **beide Container-Packages** öffentlich
   sein; die Package-Sichtbarkeit separat prüfen. Für private Auslieferung benötigt die
   Installation einen eigenen GitHub-PAT (classic) mit `read:packages` und, bei privatem
   Repository, zusätzlich `repo`, sowie den zugehörigen GitHub-Benutzernamen.
   Dieser Lesetoken ist unabhängig vom kurzlebigen CI-Token und vom Signierschlüssel.
6. Release-Anhänge und Image-Digests nicht bereinigen, solange Installationen sie benötigen.
   Die `update.json` liegt dauerhaft am Release, nicht als ablaufendes Actions-Artefakt.
   Der Updater ruft sie über die GitHub-API ab und folgt erlaubten GitHub-CDN-Weiterleitungen
   ohne Authentifizierungstoken. Er akzeptiert nur signierte Digests aus dem konfigurierten
   Container-Repository.

Nach erfolgreichen Tests und Builds erstellt der Job zunächst einen Releaseentwurf,
lädt die signierte `update.json` und den fertigen Server-Installer `install.sh` hoch und
veröffentlicht erst danach den Release. Der Installer enthält den Installationscode aus
dem getesteten Commit, genau dieses signierte Manifest und den zugehörigen öffentlichen
Prüfschlüssel; keine privaten Schlüssel oder GitHub-Tokens. Die Vorlage `scripts/install.sh`
im Repository ist allein nicht ausführbar und verweist auf den Release-Download.
Bei einem Uploadfehler bleibt der Entwurf unveröffentlicht; den Fehler beheben und den
Entwurf prüfen, bevor ein neuer Versuch erfolgt. Veröffentlichte Releases werden nicht
überschrieben. Der Manifestzeitpunkt stammt aus dem getesteten Commit; die Sichtbarkeit
in der Updatesuche richtet sich zusätzlich nach dem Veröffentlichungsdatum des Releases.

Code, Reviews, CI und Releases werden auf GitHub gepflegt. Nach einem GitHub-Merge lokal
aktualisieren und `python3 scripts/push.py` ausführen, um den Branch auch nach GitLab zu
sichern. Das Skript überträgt nur den aktuellen Branch; Tags separat sichern. Issues,
PR-Kommentare, Release-Anhänge, Images und Secrets sind nicht Teil dieser Git-Codekopie.

## Serverinstallation mit einem Befehl

Dieser Weg richtet eine **frische, dedizierte Ubuntu-24.04-EC2-Instanz** ein. Unterstützt
werden amd64 und arm64, mindestens 16 GiB RAM und 30 GiB freier Speicher unter `/opt`.
Für einen gemeinsam genutzten Server, andere Linux-Versionen, private Packages oder den
Import vorhandener Daten den manuellen Installer im nächsten Abschnitt verwenden.

### Einmal in AWS vorbereiten

1. Ubuntu 24.04 starten, ausreichend Speicher zuweisen und SSH-/sudo-Zugang bereitstellen.
   Eine öffentliche, möglichst beständige IP und eine Domain wie `video.example.com`
   verwenden. A-/gegebenenfalls AAAA-Einträge müssen auf diese Instanz zeigen; keine
   veralteten IPv6-Einträge stehen lassen.
2. In der Security Group TCP 80 und 443 für Webzugriff freigeben. SSH 22 nur für den
   eigenen Verwaltungszugang erlauben. Ausgehend werden DNS und HTTPS zu GitHub, GHCR,
   Ubuntu-/Docker-Paketquellen, dem Zertifikatsanbieter und AWS benötigt. Das Skript
   verändert keine AWS-Security-Groups oder DNS-Einträge.
3. Eine IAM-Rolle mit den benötigten Bedrock-Berechtigungen und Zugriff auf das gewählte
   Modell/Inference Profile an die Instanz hängen. In den Metadatenoptionen **IMDS aktiviert,
   IMDSv2 erforderlich und Response Hop Limit 2** einstellen, damit die Container die
   Rollen-Credentials beziehen können. Es werden keine langlebigen AWS-Schlüssel abgefragt
   oder gespeichert. Modellzugang und Berechtigungen für die verwendete Region bleiben
   Betreiberaufgaben.
4. Auf Herausgeberseite müssen ein signierter GitHub-Release mit `install.sh` und beide
   öffentlichen GHCR-Packages vorhanden sein. Der Download-Link funktioniert davor nicht.

AWS beschreibt die [Webserver-Security-Group-Regeln](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html)
und die [Metadatenoptionen für Container](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-options.html).

### Installieren

Per SSH anmelden und im eigenen Home-Verzeichnis ausführen:

```bash
curl -fsSL --proto '=https' https://github.com/MartenKiehn/Kiehnema/releases/latest/download/install.sh -o kiehnema-install.sh && sudo bash ./kiehnema-install.sh
```

Für eine festgelegte Version `latest/download` durch `download/vMAJOR.MINOR.PATCH` ersetzen.
Der Befehl lädt die Datei zunächst vollständig herunter und startet sie nur bei erfolgreichem
Download. Wer sie vorher prüfen möchte, führt Download und `sudo bash ./kiehnema-install.sh`
getrennt aus. `bash ./kiehnema-install.sh --help` benötigt keine Root-Rechte.

Das Skript prüft Betriebssystem, Architektur, Ressourcen, vorhandene Docker-Container,
das Zielverzeichnis und freie Ports. Es fragt Domain (ohne `https://`), AWS-Region und
Bedrock-Modell-/Inference-Profile-ID ab. Nach Eingabe von `INSTALL` führt es aus:

- Docker Engine und Compose aus dem offiziellen Docker-APT-Repository installieren,
  sofern Docker noch fehlt; vorhandene Versionen auf Kompatibilität prüfen.
- Docker für den Systemstart aktivieren. Host-Swap abschalten, Swap-Zeilen in `/etc/fstab`
  auskommentieren (Original: `/etc/fstab.kiehnema-before-install`) und `swap.target` maskieren.
- Ubuntus zusätzliche User-Namespace-Einschränkung über
  `/etc/sysctl.d/90-kiehnema.conf` mit `kernel.apparmor_restrict_unprivileged_userns=0`
  für Chromiums Sandbox anpassen. **Diese Einstellung gilt hostweit**; deshalb nur einen
  dedizierten Server verwenden. AppArmor bleibt aktiviert; Container behalten Seccomp,
  Chromium-Sandbox, entfernte Capabilities und `no-new-privileges`.
- Die Release-Signatur vor dem Ausführen von Container-Code prüfen, Images anhand ihrer
  Digests laden und die Verfügbarkeit der IAM-Rollen-Credentials aus einem Container testen.
- Unter `/opt/kiehnema` App, Updater, Gateway und private Konfiguration einrichten.
  Der bestehende Installer fragt Admin-Benutzername und Passwort interaktiv ab und zeigt
  den persönlichen Recovery-Key. Diesen unabhängig vom Server sichern. 2FA lässt sich
  anschließend unter **Users** aktivieren.
- Container starten, die interne Betriebsbereitschaft prüfen und HTTPS `/login` aufrufen.
  Caddy richtet das Zertifikat ein. Docker startet die Container auch nach Serverneustarts.

Es wird kein kostenpflichtiger Modellaufruf als Installationstest ausgeführt. Der IAM-Test
bestätigt die Credential-Erreichbarkeit, nicht sämtliche Bedrock-Modellberechtigungen.
Der bestehende Installer prüft außerdem Chromium und FFmpeg in der Laufzeitumgebung.

Der initial heruntergeladene Installer ist die Vertrauensbasis: GitHub-Repository,
Release-Veröffentlichung und HTTPS-Download müssen vertrauenswürdig sein. Ein mitgelieferter
öffentlicher Schlüssel schützt nicht vor einem vollständig ausgetauschten Installer.
Der private Signierschlüssel bleibt beim Herausgeber und wird niemals auf EC2 benötigt.

### Fehler und erneuter Aufruf

Ein vorhandenes `/opt/kiehnema` oder fremde Docker-Container werden nicht überschrieben.
Bei einem Fehler bleiben angelegte Installationsdaten und bereits vorgenommene Host-Änderungen
zur Diagnose erhalten; es erfolgt kein automatisches Entfernen von Paketen oder Daten.
Scheitert nur die abschließende HTTPS-Prüfung, laufen die Container weiter: DNS, Ports und
Zertifikatsausstellung prüfen. Das Zielverzeichnis nicht zum Erzwingen eines erneuten Laufs
löschen. Bei erfolgreicher Installation spätere Releases ausschließlich über **Updates**
einspielen; der Ein-Befehl-Installer ist kein Updatekommando. Für Änderungen am Betriebssystem
und Docker bleibt der Betreiber verantwortlich.

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
{"username":"GITHUB_USERNAME","token":"GITHUB_READ_TOKEN"}
```

```bash
sudo python3 scripts/install.py \
  --release /root/setup/update.json \
  --keys /root/setup/release-keys.json \
  --app-env /root/setup/app.env \
  --credentials /root/setup/update-credentials.json \
  --origin https://video.example.com
```

Bei öffentlichem Repository und öffentlichen GHCR-Packages `--credentials` weglassen. Mit `--repository`,
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

### Über GitHub Actions

1. Die aktuelle **Projektversion** steht ganz oben in der README; Quelle ist
   `pyproject.toml`. Der manuelle Release-Workflow aktualisiert beide zusammen. Die
   Projektversion ist nicht zwingend schon veröffentlicht, etwa nach fehlgeschlagenen Tests.
2. **Actions → CI → Run workflow**, Branch **main**, öffnen. Unter `version` die gewünschte
   stabile Version ohne `v` eingeben, beispielsweise `0.2.0`; unter `notes` die Release Notes.
   Versionen kleiner als die aktuelle Projektversion und vorhandene Tags werden abgelehnt.
   Für den ersten Release darf die vorhandene Projektversion verwendet werden, solange ihr
   Tag noch nicht existiert. Auch `deploy/release.json` muss vorab zum Release passen.
3. **Run workflow** starten und gegebenenfalls das Environment freigeben. Die Vorbereitung
   zeigt in den Logs und der Lauf-Zusammenfassung **aktuelle Version → gewünschte Version**.
   GitHub kann die Version aus `pyproject.toml` nicht dynamisch in den Eingabedialog laden;
   dessen Beschreibung verweist deshalb auf die Anzeige in der README.
4. Der Workflow ändert Version, README-Anzeige und Release Notes, führt `uv lock` aus,
   erstellt einen Release-Commit auf `main` und einen annotierten `vVERSION`-Tag. Beide
   Referenzen werden gemeinsam atomar nach GitHub gepusht. Hat jemand inzwischen `main`
   geändert, wird der Push abgelehnt, ohne nur eine der beiden Referenzen zu veröffentlichen.
5. Im **selben Workflow-Lauf** werden genau dieser Commit auf AMD64/ARM64 getestet,
   seine Images gebaut und der signierte Release samt Installer veröffentlicht.
   Der automatische `GITHUB_TOKEN` erzeugt bei seinen Commit-/Tag-Pushes keine zusätzlichen
   Push-Workflows. Es wird dafür kein weiterer persönlicher Token benötigt.

Scheitern Tests oder die Veröffentlichung, bleiben der vorbereitete Commit und Tag bestehen.
Ein erneuter Lauf nur der fehlgeschlagenen Jobs kann denselben Commit weiterverwenden,
solange `main` unverändert ist und noch kein Releaseentwurf oder Release existiert.
Bei vorhandenem Entwurf zuerst die Ursache und den Entwurf prüfen. Nach Änderungen am
Code eine neue Version vorbereiten; Tags werden niemals verschoben oder überschrieben.
Wie bei allen CI-Läufen bricht ein neuer Push oder manueller Start den bisherigen Lauf ab.

Die Vorbereitung schreibt ausschließlich nach GitHub. Anschließend die neue `main`-Version
lokal per Fast-forward von GitHub holen und `python3 scripts/push.py` für die GitLab-Sicherung
ausführen; den Tag zusätzlich nach GitLab pushen. GitLab-Zugangsdaten werden nicht in GitHub
hinterlegt. Der herunterladbare Installer setzt weiterhin öffentlich erreichbare Release-
Anhänge und Packages voraus; private Auslieferung benötigt eine gesonderte Authentifizierung.

### Alternativ: Version lokal vorbereiten und Tag pushen

1. Version in `pyproject.toml` und die Anzeige oben in `README.md` auf eine neue stabile
   `MAJOR.MINOR.PATCH` erhöhen und `uv lock` ausführen. Änderungen in
   `deploy/release-notes.md` beschreiben.
2. `deploy/release.json` prüfen: älteste unterstützte App-/Updater-Version,
   Ausgangs-Datenschemata und Plattformen müssen tatsächlich getestet sein.
3. Tests und Upgradeprobe durchführen, Änderungen committen und mit
   `python3 scripts/push.py` nach GitLab und GitHub pushen.
4. Den passenden geschützten Tag nach GitHub pushen, beispielsweise für Version `0.2.0`:

   ```bash
   git tag -a v0.2.0 -m 'Release 0.2.0'
   git push git@github.com:MartenKiehn/Kiehnema.git refs/tags/v0.2.0
   # Optionale Sicherung des Tags; löst in GitLab keinen Prozess aus:
   git push git@git.timeglass.de:mkiehn/kiehnema.git refs/tags/v0.2.0
   ```

   Der GitHub-Tag-Push startet Tests und danach den Releasejob. Nach der Environment-
   Freigabe erzeugt dieser die GHCR-Images und den signierten GitHub-Release.
   Ohne Signierschlüssel und geschützten Tag schlägt die Veröffentlichung geschlossen fehl.

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
Linux-Hostkonfiguration, Host-Neustart/Stromausfall, privaten GitHub-/Registry-Zugriff,
Signierschlüssel und eine zweite unabhängige Installation abnehmen. Die lokale Dockerprobe
ersetzt diese Infrastrukturabnahme nicht. Live-Bedrock-Aufrufe sind für die Updateprüfungen unnötig.

Referenzen: [GitHub Releases API](https://docs.github.com/en/rest/releases/releases),
[GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[Compose env_file](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/),
[Docker Mehrplattform-Builds](https://docs.docker.com/build/building/multi-platform/).
