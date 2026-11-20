# AgentHub – Egységes milestone fejlesztési ütemterv

**Fejlesztési roadmap · 1.2 · 2026. november 20.**

Alapelv: az első mérföldkő végére legyen egy valóban használható, végigjárható rendszer. Az MVP az M1; utána a fejlesztés azonnal folytatódik. A későbbi mérföldkövek ezt bővítik; nem szükséges mindent az első verzióba beépíteni.

## Hogyan használjuk ezt a tervet?

A milestone-ok egyetlen közös fejlesztési útvonalat alkotnak. Mindegyik a korábbi eredményére épül. Egy mérföldkő akkor kész, ha a feladatlista és az elfogadási feltételek teljesültek, és az eredmény ellenőrizhető.

## Rögzített alapelvek

- Python backend FastAPI-val, PostgreSQL adatbázissal; React + TypeScript frontend; Docker Compose.
- Egy VM-en egy önálló AgentHub-telepítés fut, saját adatbázissal és konfigurációval.
- A Coordinator és a Worker külön, látható terminálból, kézzel indított Claude Code CLI sessionként működik.
- Egy projekthez egy Worker tartozik; egy projekten belül nincs párhuzamos ticketfeldolgozás.
- A Claude CLI-t a felhasználó indítja újra; a Worker alapértelmezésben megpróbálja folytatni a félbehagyott ticketet, ez konfigurálható.
- A projektmemória végső formája hibrid: adatbázis + Markdown fájlok.
- A konfiguráció weben és YAML/JSON fájlokban is kezelhető legyen; ez fokozatosan épül ki.
- A külső írások jóváhagyáshoz kötöttek; jóváhagyás után az író adapter végrehajthatja őket. A projektizoláció és titokvédelem nem halasztható el.
- Várakozás és polling nem fogyaszt tokent: az `agenthub ... wait` parancs vár, a modell csak valódi munkánál fut (SPECIFICATION 5.1).
- A GitHub main/latest a telepítési és frissítési csatorna.

Fontos: a Claude Code skill-felismerés, session resume és modellváltás pontos működését a fejlesztés elején, a használt verzióval kell kipróbálni. Nem építünk nem dokumentált CLI-képességre.

## Milestone státuszösszesítő

| Milestone | Státusz | Megjegyzés / dátum |
| --- | --- | --- |
| M0 – Technikai validáció | Kész | Mérve, lásd docs/M0-VALIDATION.md (2026-11-20) |
| M1 – Első működő AgentHub | Kész | E2E mérve valódi Claude-sessionnel (2026-11-20) |
| M2 – Többprojekt és izoláció | Kész | Izoláció API-tesztekkel és Claude permission-méréssel |
| M3 – Emberi közreműködés és jóváhagyás | Kész | Kérdés/válasz, jóváhagyás, író adapter (mock) |
| M4 – Projektmemória | Kész | DB + Markdown, Knowledge Review |
| M5 – Konfiguráció és adapterbővítés | Kész | Élő Trello/Jira/IMAP ellen nem mérve |
| M6 – Üzemeltetés és kiadható termék | Részben | Telepítés, frissítés, mentés/visszaállítás mérve ezen a VM-en; tiszta VM-en nem |

## M0 – Technikai validáció

### Cél

Csak a legfontosabb technikai bizonytalanságokat tisztázzuk, mielőtt az alkalmazás nagyobb részét megírjuk.

### Előfeltétel

Nincs; kis prototípusokkal indul.

### Feladatlista

- [x] Claude Code aktuális skill-formátumának és telepítési helyének ellenőrzése.
- [x] Egy minimális /coordinator és /worker skill kézi indítása külön terminálból.
- [x] Bizonyítani, hogy a skill képes az AgentHub API-t meghívni és választ feldolgozni.
- [x] Session ID és dokumentált session-resume folyamat kipróbálása; CLI kézi újraindításával.
- [x] Projektkönyvtár és API-hozzáférés korlátozásának prototípusos tesztje.
- [x] A használt Claude Code verzió modellválasztási lehetőségeinek feljegyzése.
- [x] Modellhívás nélküli várakozás kipróbálása: háttérparancs befejeződésére ébredés, illetve az előtérparancs időkorlátja; egy üres ciklus tokenköltségének mérése.
- [x] Ticketenkénti friss kontextus (alügynök) kipróbálása a Workerben.
- [x] Claude Code permission-szabály, amely a Workert a projekt working directoryjára korlátozza.

**Elvárt eredmény:** Egy kicsi, eldobható prototípus és rövid technikai döntésnapló.

**Elfogadási feltétel:** A két skill kézzel elindul és API-val kommunikál; a session-folytatás működése vagy korlátja ismert; a tétlen várakozás modellhívás nélkül, vagy mért, elhanyagolható költséggel működik; nincs szükség nem dokumentált CLI-flagre.

## M1 – Első működő AgentHub

### Cél

Legyen egy valódi, végigjárható rendszer egyetlen projektre. Ez az első nagy cél, nem pusztán technikai váz.

### Előfeltétel

M0 alapvető technikai kérdései tisztázva.

### Feladatlista

- [x] FastAPI + PostgreSQL backend és minimális React/TypeScript Dashboard Docker Compose-ban.
- [x] Egy projekt regisztrálása repositoryval és working directoryval.
- [x] Minimális ticketmodell: cím, leírás, prioritás, státusz, projekt, létrehozási idő és eredmény.
- [x] Ticket státuszok a SPECIFICATION 8.0 átmenettáblája szerint, szerveroldali állapotgéppel, eseménytörténettel.
- [x] Dashboard-kulcs: a telepítés/indítás kiírja, a Dashboard bekéri és cookie-ban tárolja; agentkulcs a Coordinatornak és a projekt Workerének.
- [x] `agenthub coordinator wait` és `agenthub worker wait`: várakozás és claim modellhívás nélkül.
- [x] Manuális ticketfelvétel a Dashboardról; külső forrásintegráció még nem feltétel.
- [x] Kézzel indítható /coordinator és /worker skill külön terminálból.
- [x] Coordinator minimális feladata: a manuális ticketek áttekintése és READY állapotba rendezése.
- [x] Mehet jelölés a ticketen (alapból kikapcsolva; ember kapcsolhatja a Dashboardon, a Coordinator indoklással sürgős, halaszthatatlan hibánál); a Worker csak bepipált ticketet vehet fel, és nem módosíthatja a jelölést.
- [x] Worker: egy READY és bepipált ticket felvétele, feldolgozása a projektben, eredmény és státusz visszaírása.
- [x] Projekt szintű egyetlen Worker és egyetlen aktív ticket biztosítása.
- [x] Alapvető hiba- és megszakításkezelés: ticket állapotának megőrzése, újraindítás után folytatási kísérlet.
- [x] Dashboard a meglévő feladatlap-dashboard alapján: Kanban, Mehet checkbox a kártyán, ticket részletező, új ticket, Worker/Coordinator állapot és Leállít/Engedélyez kapcsoló, élő frissítés SSE-vel.

**Elvárt eredmény:** Egy minimális, de ténylegesen használható AgentHub, amelyben manuálisan létrehozott feladatot a Worker elvégez, az eredmény pedig megjelenik a Dashboardon.

**Elfogadási feltétel:** Friss indítás után a felhasználó el tudja indítani a rendszert; létrehoz egy ticketet és bepipálja a Mehet jelölést; elindítja a Claude CLI-t és a Workert; bepipálatlan ticketet a Worker nem vesz fel; a Worker elvégzi a feladatot; a ticket és az eredmény visszakerül az adatbázisba és látszik a Dashboardon. Nincs párhuzamos feldolgozás.

## M2 – Többprojekt és izoláció

### Cél

A már működő egyrendszeres alap több projektet kezeljen egy AgentHub-telepítésen belül.

### Előfeltétel

M1 működő end-to-end folyamat.

### Feladatlista

- [x] Több projekt felvétele saját repositoryval és working directoryval.
- [x] Projektenként külön Worker-konfiguráció és külön terminál/session.
- [x] Projektazonosító kötelező érvényesítése a ticket- és agent API-kban.
- [x] Worker csak saját projekt ticketjeit (projekthez kötött kulcs) és könyvtárát (Claude Code permission-szabályok) érheti el.
- [x] Projektmemória és konfiguráció elkülönítése már az alapstruktúrában.
- [x] Negatív tesztek: másik projekt ticketjének, fájljainak és adatainak elérése tiltott.

**Elvárt eredmény:** Többprojekt-telepítés, amelyben minden projekt önálló Workerrel működik.

**Elfogadási feltétel:** Két külön projekt Workerje csak saját projektjében dolgozik; egyik sem tud másik projekt ticketet claimelni (API szinten kikényszerítve), és a Claude Code permission-szabályai megtagadják a másik projekt repositoryjához való hozzáférést.

## M3 – Emberi közreműködés és jóváhagyás

### Cél

A Worker tudjon kérdezni, és a külső rendszerek módosítása maradjon emberi kontroll alatt.

### Előfeltétel

M2 projektizolációja és M1 ticketfolyamata.

### Feladatlista

- [x] Tickethez kapcsolt kérdés és válasz tárolása.
- [x] WAITING_FOR_INPUT állapot és válasz után READY állapotba visszatérés.
- [x] Dashboardon szabad szöveges válasz és Worker-javaslat elfogadása.
- [x] Egyszerű PendingAction lista külső műveleti javaslatokhoz.
- [x] Jóváhagyás/elutasítás szerveroldali ellenőrzése.
- [x] E-mail, Jira vagy Trello művelet előkészítése; jóváhagyás nélkül ne történjen külső írás.
- [x] Jóváhagyás után az író adapter végrehajtja a jóváhagyott payloadot (APPROVED → EXECUTED/FAILED), ahol van író adapter.
- [x] Manuálisan végrehajtott művelet visszaigazolásának rögzítése.

**Elvárt eredmény:** Használható emberi visszacsatolás és egyszerű approval workflow.

**Elfogadási feltétel:** A Worker kérdésére adott válasz után a ticket folytatható; jóváhagyás nélkül egy külső írási művelet sem hajtható végre.

## M4 – Projektmemória

### Cél

A Worker a projekt korábbi tudását és tapasztalatait fel tudja használni, projektközi keveredés nélkül.

### Előfeltétel

M2 projektizoláció; M3 Dashboardos emberi kontroll előnyös.

### Feladatlista

- [x] Projektmemória tárolása PostgreSQL-ben és Markdown-fájlokban.
- [x] Projekt meglévő CLAUDE.md, .claude/skills, .claude/commands és dokumentáció leltározása.
- [x] Ticketekből származó tudásjavaslatok létrehozása.
- [x] Dashboardon Knowledge Review: elfogadás, szerkesztés, elutasítás.
- [x] Elfogadott tudás szinkronizálása a projektmemória fájljaival.
- [x] Meglévő projektfájl módosítása előtt diff, mentés és emberi jóváhagyás.
- [x] Projektmemória hozzáférésének tesztelése projektazonosító alapján.

**Elvárt eredmény:** Jóváhagyással bővülő, DB + Markdown alapú projektmemória.

**Elfogadási feltétel:** A Worker csak a saját projekt memóriáját látja; új tudás nem kerül be automatikusan emberi felülvizsgálat nélkül.

## M5 – Konfiguráció és adapterbővítés

### Cél

A kezdeti, egyszerű beállításokat fokozatosan rugalmasan kezelhető rendszerré bővítsük.

### Előfeltétel

M1 stabil működése; M2 többprojekt; a forrásadaptereknél M3 approval védelme.

### Feladatlista

- [x] YAML/JSON konfigurációs formátumok véglegesítése és validálása.
- [x] Dashboardos szerkesztés a legfontosabb projekt- és Worker-beállításokhoz.
- [x] Konfiguráció import/export és alap változásnapló.
- [x] Első szükséges külső forrásadapter(ek) bevezetése, például e-mail, Trello vagy Jira. (IMAP, Trello, Jira olvasó és író adapter; élő szolgáltatás ellen még nem, csak mockolt HTTP-vel tesztelve.)
- [x] Általános CLI-adapter csak engedélyezett executable-lel, argumentumlistával és JSON-sémával.
- [x] Poll intervallum, forrás-hozzárendelés és aktív/inaktív állapot konfigurálása.
- [x] Alap modellstratégia és egyszerű futási/tokenstatisztika.

**Elvárt eredmény:** Dashboardról és fájlból is kezelhető konfiguráció, bővíthető forrásintegrációkkal.

**Elfogadási feltétel:** A konfiguráció hibás állapotban nem aktiválódik; az adapterek csak engedélyezett parancsot futtatnak; a külső írás jóváhagyási szabálya változatlanul érvényes.

## M6 – Üzemeltetés és kiadható termék

### Cél

A rendszert tiszta VM-re telepíthető, frissíthető és helyreállítható GitHub-projektté alakítjuk.

### Előfeltétel

M1–M5 fő funkciói stabilak.

### Feladatlista

- [x] GitHub main/latest telepítési folyamat és egyszerű install script.
- [x] Frissítés előtt adatbázis- és konfigurációmentés.
- [x] Backup/restore parancsok és dokumentáció.
- [x] DB migrációk és frissítés utáni health check.
- [x] Titkok maszkolása, Dashboard hozzáférés és hálózati beállítások hardeningje.
- [x] Alap logok, agent heartbeat, futási idő és becsült költség megjelenítése.
- [x] Teszt: Worker megszakadása, lease lejárat, ticket folytatása és session fallback.
- [x] End-to-end teszt: forrás vagy manuális ticket → Coordinator → Worker → eredmény → Dashboard.
- [ ] Tiszta Ubuntu VM-en telepítési és visszaállítási próba. (A fejlesztő VM-en, külön könyvtárba telepítve mérve; tiszta VM-en még nem.)

**Elvárt eredmény:** Első kiadható, önállóan telepíthető AgentHub GitHub repository.

**Elfogadási feltétel:** Tiszta VM-en telepíthető; frissíthető és mentésből visszaállítható; az alapvető biztonsági és end-to-end tesztek sikeresek.

## Végső ellenőrzőlista

- [x] Az első működő mérföldkő valódi ticketet tud feldolgozni az elejétől az eredményig.
- [x] A Coordinator és Worker külön, látható terminálból, kézzel indul.
- [x] Projektenként egy Worker és egy aktív feldolgozás érvényesül.
- [x] A Worker csak a saját projektjéhez fér hozzá.
- [x] A félbehagyott ticket alapértelmezésben folytatható, de a viselkedés konfigurálható.
- [x] Az emberi kérdések és válaszok tartósan megmaradnak.
- [x] Külső írás csak jóváhagyással történhet.
- [x] A projektmemória DB + Markdown alapú és projektenként izolált.
- [x] A konfiguráció weben és fájlból is kezelhető.
- [x] A rendszer GitHubról telepíthető, frissíthető és helyreállítható.

## Fejlesztési munkamódszer Claude Code-dal

- Egyszerre csak az aktuális milestone feladatát kapja meg Claude Code.
- Először tervet készítsen, majd implementáljon; a tervet ember ellenőrizze.
- Minden milestone végén tesztek, rövid jelentés és Git commit készüljön.
- A következő milestone csak az előző elfogadási feltételeinek teljesülése után induljon.
- A biztonságot, projektizolációt, titkokat vagy meglévő projektfájlokat érintő változás emberi felülvizsgálatot igényel.

Ez a roadmap egyetlen folyamatos fejlesztési terv. Az M1 a legfontosabb eredmény: legyen egy működő AgentHub. A további milestone-ok ezt bővítik, nem előfeltételei annak, hogy az első használható verzió megszülessen.
