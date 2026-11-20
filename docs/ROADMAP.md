# AgentHub – Egységes milestone fejlesztési ütemterv

**Fejlesztési roadmap · 1.1 · 2026. november 20.**

Alapelv: az első mérföldkő végére legyen egy valóban használható, végigjárható rendszer. A későbbi mérföldkövek ezt bővítik; nem szükséges mindent az első verzióba beépíteni.

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
- A külső írások jóváhagyáshoz kötöttek; a projektizoláció és titokvédelem nem halasztható el.
- A GitHub main/latest a telepítési és frissítési csatorna.

Fontos: a Claude Code skill-felismerés, session resume és modellváltás pontos működését a fejlesztés elején, a használt verzióval kell kipróbálni. Nem építünk nem dokumentált CLI-képességre.

## Milestone státuszösszesítő

| Milestone | Státusz | Megjegyzés / dátum |
| --- | --- | --- |
| M0 – Technikai validáció | Nem kezdődött el | |
| M1 – Első működő AgentHub | Nem kezdődött el | |
| M2 – Többprojekt és izoláció | Nem kezdődött el | |
| M3 – Emberi közreműködés és jóváhagyás | Nem kezdődött el | |
| M4 – Projektmemória | Nem kezdődött el | |
| M5 – Konfiguráció és adapterbővítés | Nem kezdődött el | |
| M6 – Üzemeltetés és kiadható termék | Nem kezdődött el | |

## M0 – Technikai validáció

### Cél

Csak a legfontosabb technikai bizonytalanságokat tisztázzuk, mielőtt az alkalmazás nagyobb részét megírjuk.

### Előfeltétel

Nincs; kis prototípusokkal indul.

### Feladatlista

- [ ] Claude Code aktuális skill-formátumának és telepítési helyének ellenőrzése.
- [ ] Egy minimális /coordinator és /worker skill kézi indítása külön terminálból.
- [ ] Bizonyítani, hogy a skill képes az AgentHub API-t meghívni és választ feldolgozni.
- [ ] Session ID és dokumentált session-resume folyamat kipróbálása; CLI kézi újraindításával.
- [ ] Projektkönyvtár és API-hozzáférés korlátozásának prototípusos tesztje.
- [ ] A használt Claude Code verzió modellválasztási lehetőségeinek feljegyzése.

**Elvárt eredmény:** Egy kicsi, eldobható prototípus és rövid technikai döntésnapló.

**Elfogadási feltétel:** A két skill kézzel elindul és API-val kommunikál; a session-folytatás működése vagy korlátja ismert; nincs szükség nem dokumentált CLI-flagre.

## M1 – Első működő AgentHub

### Cél

Legyen egy valódi, végigjárható rendszer egyetlen projektre. Ez az első nagy cél, nem pusztán technikai váz.

### Előfeltétel

M0 alapvető technikai kérdései tisztázva.

### Feladatlista

- [ ] FastAPI + PostgreSQL backend és minimális React/TypeScript Dashboard Docker Compose-ban.
- [ ] Egy projekt regisztrálása repositoryval és working directoryval.
- [ ] Minimális ticketmodell: cím, leírás, prioritás, státusz, projekt, létrehozási idő és eredmény.
- [ ] Alap ticket státuszok és eseménytörténet.
- [ ] Manuális ticketfelvétel a Dashboardról; külső forrásintegráció még nem feltétel.
- [ ] Kézzel indítható /coordinator és /worker skill külön terminálból.
- [ ] Coordinator minimális feladata: a manuális ticketek áttekintése és READY állapotba rendezése.
- [ ] Worker: egy READY ticket felvétele, feldolgozása a projektben, eredmény és státusz visszaírása.
- [ ] Projekt szintű egyetlen Worker és egyetlen aktív ticket biztosítása.
- [ ] Alapvető hiba- és megszakításkezelés: ticket állapotának megőrzése, újraindítás után folytatási kísérlet.
- [ ] Dashboardon ticketlista/Kanban és az aktuális Worker állapota.

**Elvárt eredmény:** Egy minimális, de ténylegesen használható AgentHub, amelyben manuálisan létrehozott feladatot a Worker elvégez, az eredmény pedig megjelenik a Dashboardon.

**Elfogadási feltétel:** Friss indítás után a felhasználó el tudja indítani a rendszert; létrehoz egy ticketet; elindítja a Claude CLI-t és a Workert; a Worker elvégzi a feladatot; a ticket és az eredmény visszakerül az adatbázisba és látszik a Dashboardon. Nincs párhuzamos feldolgozás.

## M2 – Többprojekt és izoláció

### Cél

A már működő egyrendszeres alap több projektet kezeljen egy AgentHub-telepítésen belül.

### Előfeltétel

M1 működő end-to-end folyamat.

### Feladatlista

- [ ] Több projekt felvétele saját repositoryval és working directoryval.
- [ ] Projektenként külön Worker-konfiguráció és külön terminál/session.
- [ ] Projektazonosító kötelező érvényesítése a ticket- és agent API-kban.
- [ ] Worker csak saját projekt ticketjeit és könyvtárát érheti el.
- [ ] Projektmemória és konfiguráció elkülönítése már az alapstruktúrában.
- [ ] Negatív tesztek: másik projekt ticketjének, fájljainak és adatainak elérése tiltott.

**Elvárt eredmény:** Többprojekt-telepítés, amelyben minden projekt önálló Workerrel működik.

**Elfogadási feltétel:** Két külön projekt Workerje csak saját projektjében dolgozik; egyik sem tud másik projekt ticketet claimelni vagy repositoryjához hozzáférni.

## M3 – Emberi közreműködés és jóváhagyás

### Cél

A Worker tudjon kérdezni, és a külső rendszerek módosítása maradjon emberi kontroll alatt.

### Előfeltétel

M2 projektizolációja és M1 ticketfolyamata.

### Feladatlista

- [ ] Tickethez kapcsolt kérdés és válasz tárolása.
- [ ] WAITING_FOR_INPUT állapot és válasz után READY állapotba visszatérés.
- [ ] Dashboardon szabad szöveges válasz és Worker-javaslat elfogadása.
- [ ] Egyszerű PendingAction lista külső műveleti javaslatokhoz.
- [ ] Jóváhagyás/elutasítás szerveroldali ellenőrzése.
- [ ] E-mail, Jira vagy Trello művelet előkészítése, de jóváhagyás nélkül ne történjen külső írás.
- [ ] Manuálisan végrehajtott művelet visszaigazolásának rögzítése.

**Elvárt eredmény:** Használható emberi visszacsatolás és egyszerű approval workflow.

**Elfogadási feltétel:** A Worker kérdésére adott válasz után a ticket folytatható; jóváhagyás nélkül egy külső írási művelet sem hajtható végre.

## M4 – Projektmemória

### Cél

A Worker a projekt korábbi tudását és tapasztalatait fel tudja használni, projektközi keveredés nélkül.

### Előfeltétel

M2 projektizoláció; M3 Dashboardos emberi kontroll előnyös.

### Feladatlista

- [ ] Projektmemória tárolása PostgreSQL-ben és Markdown-fájlokban.
- [ ] Projekt meglévő CLAUDE.md, .claude/skills, .claude/commands és dokumentáció leltározása.
- [ ] Ticketekből származó tudásjavaslatok létrehozása.
- [ ] Dashboardon Knowledge Review: elfogadás, szerkesztés, elutasítás.
- [ ] Elfogadott tudás szinkronizálása a projektmemória fájljaival.
- [ ] Meglévő projektfájl módosítása előtt diff, mentés és emberi jóváhagyás.
- [ ] Projektmemória hozzáférésének tesztelése projektazonosító alapján.

**Elvárt eredmény:** Jóváhagyással bővülő, DB + Markdown alapú projektmemória.

**Elfogadási feltétel:** A Worker csak a saját projekt memóriáját látja; új tudás nem kerül be automatikusan emberi felülvizsgálat nélkül.

## M5 – Konfiguráció és adapterbővítés

### Cél

A kezdeti, egyszerű beállításokat fokozatosan rugalmasan kezelhető rendszerré bővítsük.

### Előfeltétel

M1 stabil működése; M2 többprojekt; a forrásadaptereknél M3 approval védelme.

### Feladatlista

- [ ] YAML/JSON konfigurációs formátumok véglegesítése és validálása.
- [ ] Dashboardos szerkesztés a legfontosabb projekt- és Worker-beállításokhoz.
- [ ] Konfiguráció import/export és alap változásnapló.
- [ ] Első szükséges külső forrásadapter(ek) bevezetése, például e-mail, Trello vagy Jira.
- [ ] Általános CLI-adapter csak engedélyezett executable-lel, argumentumlistával és JSON-sémával.
- [ ] Poll intervallum, forrás-hozzárendelés és aktív/inaktív állapot konfigurálása.
- [ ] Alap modellstratégia és egyszerű futási/tokenstatisztika.

**Elvárt eredmény:** Dashboardról és fájlból is kezelhető konfiguráció, bővíthető forrásintegrációkkal.

**Elfogadási feltétel:** A konfiguráció hibás állapotban nem aktiválódik; az adapterek csak engedélyezett parancsot futtatnak; a külső írás jóváhagyási szabálya változatlanul érvényes.

## M6 – Üzemeltetés és kiadható termék

### Cél

A rendszert tiszta VM-re telepíthető, frissíthető és helyreállítható GitHub-projektté alakítjuk.

### Előfeltétel

M1–M5 fő funkciói stabilak.

### Feladatlista

- [ ] GitHub main/latest telepítési folyamat és egyszerű install script.
- [ ] Frissítés előtt adatbázis- és konfigurációmentés.
- [ ] Backup/restore parancsok és dokumentáció.
- [ ] DB migrációk és frissítés utáni health check.
- [ ] Titkok maszkolása, Dashboard hozzáférés és hálózati beállítások hardeningje.
- [ ] Alap logok, agent heartbeat, futási idő és becsült költség megjelenítése.
- [ ] Teszt: Worker megszakadása, lease lejárat, ticket folytatása és session fallback.
- [ ] End-to-end teszt: forrás vagy manuális ticket → Coordinator → Worker → eredmény → Dashboard.
- [ ] Tiszta Ubuntu VM-en telepítési és visszaállítási próba.

**Elvárt eredmény:** Első kiadható, önállóan telepíthető AgentHub GitHub repository.

**Elfogadási feltétel:** Tiszta VM-en telepíthető; frissíthető és mentésből visszaállítható; az alapvető biztonsági és end-to-end tesztek sikeresek.

## Végső ellenőrzőlista

- [ ] Az első működő mérföldkő valódi ticketet tud feldolgozni az elejétől az eredményig.
- [ ] A Coordinator és Worker külön, látható terminálból, kézzel indul.
- [ ] Projektenként egy Worker és egy aktív feldolgozás érvényesül.
- [ ] A Worker csak a saját projektjéhez fér hozzá.
- [ ] A félbehagyott ticket alapértelmezésben folytatható, de a viselkedés konfigurálható.
- [ ] Az emberi kérdések és válaszok tartósan megmaradnak.
- [ ] Külső írás csak jóváhagyással történhet.
- [ ] A projektmemória DB + Markdown alapú és projektenként izolált.
- [ ] A konfiguráció weben és fájlból is kezelhető.
- [ ] A rendszer GitHubról telepíthető, frissíthető és helyreállítható.

## Fejlesztési munkamódszer Claude Code-dal

- Egyszerre csak az aktuális milestone feladatát kapja meg Claude Code.
- Először tervet készítsen, majd implementáljon; a tervet ember ellenőrizze.
- Minden milestone végén tesztek, rövid jelentés és Git commit készüljön.
- A következő milestone csak az előző elfogadási feltételeinek teljesülése után induljon.
- A biztonságot, projektizolációt, titkokat vagy meglévő projektfájlokat érintő változás emberi felülvizsgálatot igényel.

Ez a roadmap egyetlen folyamatos fejlesztési terv. Az M1 a legfontosabb eredmény: legyen egy működő AgentHub. A további milestone-ok ezt bővítik, nem előfeltételei annak, hogy az első használható verzió megszülessen.
