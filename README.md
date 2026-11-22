# AgentHub

Önállóan telepíthető koordinációs rendszer Claude Code-agenteknek. Projektfüggetlen: egy Ubuntu VM-en egy telepítés, saját adatbázissal, Dashboarddal, egy Coordinatorral és projektenként egy Workerrel.

- **Coordinator:** figyeli a forrásokat (e-mail, Trello, Jira, tetszőleges JSON-t adó CLI), ticketet készít, kiszűri a duplikációt és projekthez sorol. Kódot nem módosít.
- **Worker:** a saját projektje ticketjeit dolgozza fel, egyszerre egyet. Csak azt veszi fel, amit ember (vagy sürgős hibánál, indoklással, a Coordinator) **Mehet**-re jelölt.
- **Dashboard:** Kanban-tábla, ticket-részletek, kérdések és válaszok, jóváhagyások, projektmemória, agentállapot, konfiguráció. Élőben frissül.
- **Biztonság:**
  - Olvasás és előkészítés automatikus; kifelé (e-mail, Jira, Trello) csak emberi jóváhagyás után történik írás.
  - A projektek egymástól elszigeteltek.
  - A titkok nem kerülnek Gitbe.

Részletek: [SPECIFICATION](docs/SPECIFICATION.md) · [ROADMAP](docs/ROADMAP.md) · [M0 döntésnapló](docs/M0-VALIDATION.md)

## Telepítés

```bash
curl -fsSL https://raw.githubusercontent.com/wolfpeter/agenthub/main/scripts/install.sh | bash
```

A telepítő:
1. Ellenőrzi és szükség esetén telepíti az előfeltételeket (git, Docker, Compose, uv).
2. A repót a `~/AgentHub` könyvtárba klónozza, és telepíti az `agenthub` parancsot.
3. Kulcsokat generál, majd elindítja az API-t, a Dashboardot és a PostgreSQL-t.
4. Telepíti a `/coordinator` és `/worker` skillt. A meglévő, azonos nevű skilleket nem írja felül.
5. A végén kiírja a Dashboard címét és belépési kulcsát.

A Claude Code CLI-t (`claude`) külön kell telepíteni.

Változók: `AGENTHUB_PROJECTS_ROOT` (a Dashboard mappaválasztójának gyökere, pl. `~/Projects`; csak olvashatóan csatolódik, a Dashboard csak a könyvtárneveket látja; üresen a választó ki van kapcsolva), `AGENTHUB_HOME`, `AGENTHUB_PORT` (alap 8790), `AGENTHUB_BIND` (alap 0.0.0.0; egy címre szűkíti a figyelést), `AGENTHUB_ALLOW_PUBLIC=1` (alapból az API csak LAN-ról, Tailscale-ről és a helyi gépről fogad kérést, nyilvános internetes címről nem), `AGENTHUB_NAME` (a gép neve a böngészőfül címében: „<név> – AgentHub”), `AGENTHUB_INSTANCE`, `AGENTHUB_SKIP_SKILLS=1`. Több címre kötéshez (pl. csak localhost és Tailscale) a telepítés könyvtárában egy `compose.override.yaml` felülírhatja az `api` szolgáltatás `ports` listáját (`ports: !override`); az `agenthub` parancsok ezt is betöltik.

Ha a telepítés már megvan, ugyanez a parancs frissít: mentést készít, lehúzza a `main` ágat, migrál, majd health checket futtat.

## Használat

```bash
agenthub project add --id webshop --name "Webshop" --dir ~/Projects/webshop
agenthub coordinator run              # elindítja a Coordinatort ebben a terminálban
agenthub worker run --project webshop # elindítja a Workert (félbehagyott ticketnél a sessiont folytatja; --new: új session)
```

Az agenteket mindig kézzel kell elindítani, látható terminálban. A `run` a helyes beállításokkal (jogosultságok, modell, session-azonosító) indítja a `claude`-ot, és rögtön elindítja a `/coordinator`, illetve `/worker` skillt. A `command` ugyanezt csak kiírja, ha magad indítanád. A várakozás nem fogyaszt tokent: az `agenthub … wait` parancs vár, a modell csak valódi munkánál fut.

A Dashboardon:
- Projektet a fejléc „+ Projekt” gombjával veszel fel (mappát választasz), a Konfiguráció → Projektek listában elengeded (aktív kapcsoló) vagy törlöd (csak ticket nélküli projektet). Az elengedett projekt eltűnik a fejlécből, Worker nem indul rá, és nem sorol be oda senki. A Dashboardon felvett projekt Worker-kulcsát az első `agenthub worker run` hozza létre.
- A ticket részleteinél a „Megjegyzés a Workernek” dobozba írt szöveget a Worker utasításként kapja (a ticket `comments` mezője); a ticket állapotát nem változtatja. A fejléc ☾/☀ gombja vált világos és sötét téma között (alapból a rendszeré).
- Felveszed a ticketet, és bepipálod a **Mehet**-et. A Worker csak ezután veszi fel.
- Válaszolsz a Worker kérdéseire.
- Jóváhagyod vagy elutasítod a külső műveleteket.
- Elfogadod az eredményt (REVIEW → COMPLETED).
- Hotfixnek jelölöd a sürgős javítást: a Worker elsőként veszi fel, és a projekt utasítása szerint teszt után élesíti.

**Testreszabás a célgépen, az AgentHub módosítása nélkül:**
- `worker.instructions` (projektenként) és `coordinator_instructions` (globális): a helyi szabályok. Ütközéskor ezek az irányadók a skillek alapszabályaival szemben.
- `worker.allowed_tools`, `coordinator_allowed_tools` és a permission mode: például MCP-eszközök a közvetlen írásokhoz.
- `outbound_approval` (a Dashboard „Kimenő kommunikáció” dobozában kapcsolható) és `outbound_tools`: kikapcsolt kimenő kommunikációnál a Coordinator és a Workerek csak jóváhagyással írnak ki; az itt felsorolt eszközök a sessionjeikben tiltottak.
- `coordinator_review_every` (alap 2): a Coordinator minden N-edik körében átnézi a nyitott, nem a Workernél lévő ticketeket, és a forrás valós állapotához igazítja őket (`agenthub coordinator review`); a lezártakat nem. 0 = kikapcsolva.
- `worker.review_required: false`: a Worker emberi átvétel nélkül is lezárhat.
- `worker_gate_command`: helyi script, amely a Worker elé kapuként áll (pl. kvótafigyelés).
- CLI-forrás: bármilyen JSON-t adó script (pl. piros pipeline, figyelő scriptek).
- Élesítés: `finish --to release` → „Kész – kiadásra vár”; a kiadás után `agenthub release list` és `agenthub release done`.

| Parancs | Mire |
| --- | --- |
| `agenthub key show` / `key rotate dashboard` | Dashboard-kulcs megjelenítése, cseréje |
| `agenthub project add/list/key/enable/disable` | Projektek felvétele, listája, Worker-kulcs, munkába vétel és elengedés |
| `agenthub config export [fájl]` / `config import <fájl>` | Konfiguráció YAML-ben |
| `agenthub source test <id>` | Forrásadapter kipróbálása (adatot nem küld) |
| `agenthub backup` / `restore <fájl>` | Titkosított mentés (DB, konfiguráció, memória) és visszaállítás |
| `agenthub update` | Mentés + frissítés + migráció + health check |
| `agenthub doctor` | Állapot-ellenőrzés |
| `agenthub skills install [--force]` | Skillek (felülírás csak mentéssel) |

A forrás- és író adapterek titkai a `~/AgentHub/secrets/sources.env` fájlba kerülnek. A mentéseket a `secrets/backup.key` kulcs titkosítja; ezt a kulcsot külön is őrizd meg.

## Fejlesztés

```bash
uv sync
docker run -d --name agenthub-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=agenthub_test \
  -p 127.0.0.1:55432:5432 postgres:17-alpine
uv run pytest
cd frontend && npm ci && npm run build   # → agenthub/static
```

Felépítés:
- `agenthub/`: FastAPI-backend (`api/`), üzleti logika (`services.py`), állapotgép (`states.py`), adatmodell (`models.py`), konfigurációs séma, `cli/` (a host-oldali parancssor), `adapters/` (olvasók), `writers.py` (író adapterek).
- `migrations/`: Alembic.
- `frontend/`: React + TypeScript + Tailwind + Motion.
- `skills/`: a két Claude Code skill.
- `scripts/install.sh`: a telepítő.
