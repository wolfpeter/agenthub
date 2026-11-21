# AgentHub

**Műszaki és funkcionális specifikáció · 0.10 · 2026. november 20.**

Státusz: követelmény-specifikáció fejlesztési alapként. Ahol a Claude Code aktuális viselkedése vagy egy külső CLI képessége verziófüggő, ott a dokumentum validációs követelményt ír elő, nem feltételez garantált működést.

## 1. Cél és alapelvek

Az AgentHub önállóan telepíthető, GitHubról frissíthető AI-agent koordinációs rendszer. Feladata a bejövő igények összegyűjtése, ticketekké alakítása, projekt szerinti szétosztása, a Claude Code-alapú munkavégzés támogatása, az eredmények és események megőrzése, valamint az emberi jóváhagyást igénylő külső műveletek biztonságos kezelése.

Projektfüggetlen termék: nem tartalmazhat iDest-, AP4- vagy más konkrét projektre szabott üzleti logikát.

Egy VM = egy önálló AgentHub-telepítés, saját adatbázissal, konfigurációval, dashboarddal, Coordinatorral és Worker(ek)kel.

Egy projekthez egy Worker tartozik; egy projekten belül nincs párhuzamos ticketfeldolgozás.

A Coordinator nem fejleszt és nem módosít projekt-repositoryt; a projektmunkát a Worker végzi.

A Dashboard/API/DB szolgáltatások tartósan futhatnak; a Coordinator és a Worker Claude Code sessionjei külön, látható terminálban, kézi indítással futnak.

Az AgentHub kiegészíti a meglévő Claude Code-környezetet; nem írhatja felül automatikusan a meglévő skilleket, CLAUDE.md-t, MCP-ket vagy globális beállításokat.

Biztonságos alapbeállítás: olvasás és előkészítés automatizálható; külső írás csak jóváhagyás után.

## 2. Hatókör

### 2.1 Az első teljes kiadás része (M1–M6)

Az MVP a ROADMAP M1 mérföldköve: egy projektre, kézzel felvett ticketekkel végigjárható, ténylegesen használható rendszer. Az alábbi lista a teljes első kiadás hatóköre; az M1 után a további mérföldkövek azonnal folytatódnak.

Coordinator forrásfigyeléssel, normalizálással, deduplikációval, projektbesorolással és ticketkezeléssel.

Projektenként egy Worker, amely a projekt saját queue-jából vesz fel ticketet.

Ticketek, eseménytörténet, kérdések/válaszok, futási metaadatok és jóváhagyandó műveletek PostgreSQL-tárolása.

React-alapú webes Dashboard Kanban nézettel, agentállapotokkal, ticketrészletekkel, konfigurációval és jóváhagyási felülettel.

Webes és fájlalapú konfiguráció, validálás, import/export és változásnapló.

Projektmemória hibrid tárolása: adatbázis + projektben tárolt Markdown-dokumentumok.

GitHub main/latest telepítési és frissítési csatorna, mentéssel és visszaállítási lehetőséggel.

### 2.2 Nem cél az első verzióban

Automatikusan háttérben induló Claude Code agentek.

Projekt- vagy VM-ek közötti központi, közös adatbázis vagy memória.

Ugyanazon projekt több Workerrel történő párhuzamos feldolgozása.

Jóváhagyás nélküli e-mail-küldés vagy Jira/Trello írás.

Általános célú, tetszőleges shell-parancs végrehajtó felület.

## 3. Telepítési és futtatási modell

### 3.1 Telepítés

Az AgentHub GitHub repositoryból telepíthető. A main branch a gördülő latest csatorna. A telepítő ellenőrzi a környezetet, mentést készít, létrehozza az AgentHub saját könyvtárait és szolgáltatásait, majd inicializálja az adatbázist. A meglévő Claude Code- és projektfájlokat először csak leltározza.

Javasolt telepítési parancs: curl -fsSL https://raw.githubusercontent.com/wolfpeter/agenthub/main/scripts/install.sh | bash

Fejlesztői telepítés: git clone https://github.com/wolfpeter/agenthub.git, uv sync, frontend függőségek telepítése, docker compose up -d, agenthub init.

Alapértelmezett könyvtárstruktúra: ~/AgentHub (rendszer), ~/Projects/<project> (projektrepositoryk).

Telepítésenként saját .env/secrets, PostgreSQL-adatbázis, konfiguráció és backup.

Frissítés előtt DB- és konfigurációmentés; migráció; health check; hiba esetén visszaállítási útmutató.

A telepítő a végén egyszer kiírja a Dashboard-hozzáférési kulcsot és a Dashboard címét (lásd 18.1). A kulcs később `agenthub key rotate` paranccsal cserélhető.

### 3.2 Folyamatok

| Komponens | Indítás | Futási mód |
| --- | --- | --- |
| API + Dashboard | Docker Compose / VM indulásakor | Háttérszolgáltatás |
| PostgreSQL | Docker Compose / VM indulásakor | Háttérszolgáltatás |
| Coordinator | Felhasználó kézzel, terminálban | Interaktív Claude Code CLI + /coordinator |
| Worker | Felhasználó kézzel, projektenként terminálban | Interaktív Claude Code CLI + /worker |

A Dashboard monitoroz és konfigurál, de nem indítja és nem állítja le a Claude Code agenteket. Nem használható tmux, screen, systemd vagy más process manager a Claude CLI sessionök automatikus háttérindítására. A felhasználó RDP-n vagy más interaktív terminálon keresztül indítja a megfelelő Claude CLI-t.

Példa: az `agenthub coordinator command` és az `agenthub worker command --project <id>` kiírja a teljes indítóparancsot (`claude --session-id … --settings …`); a felhasználó ezt futtatja, majd a sessionben /coordinator, illetve /worker. A `--settings` fájl tartalmazza az agent engedélyeit és a projektizoláció tiltásait (lásd docs/M0-VALIDATION.md).

## 4. Architektúra

| Réteg | Technológia / felelősség |
| --- | --- |
| Backend API | Python 3.13+, FastAPI, Pydantic, SQLAlchemy 2, Alembic, httpx, asyncio |
| Adattár | PostgreSQL; tranzakciós ticket-claim és lease/heartbeat |
| Frontend | React + TypeScript, Tailwind CSS, Motion; REST + Server-Sent Events (SSE) |
| Python csomagkezelés | uv; pyproject.toml és uv.lock |
| Futtatás | Docker Compose az API, Dashboard statikus kiszolgálása és DB számára |
| Agent-interakció | Claude Code CLI interaktív terminálból, AgentHub által biztosított skill-ekkel |

A Python backend nem indítja el a Claude-ot, nem vezérli a terminálját és nem helyettesíti a Claude Code futtatókörnyezetét. A skill-ek az AgentHub API-val kommunikálnak. A backend felel az állapotok tartósságáért, jogosultság-ellenőrzésért, ticket claimért, eseményekért és auditért.

### 4.1 Fő komponensek

Coordinator skill: források lekérdezése, események feldolgozása, projekt-hozzárendelés, ticket létrehozása/frissítése.

Worker skill: konfiguráció betöltése, projekt queue lekérdezése, ticket claim, Claude Code-os végrehajtás, eredményrögzítés.

Source adapters: kész integrációk és korlátozott, strukturált CLI-adapterek.

Project registry: projektek, working directory, repository, branch és adapter-hozzárendelések.

Ticket service: állapotgép, deduplikáció, lease, retry, eseménynapló.

Approval service: külső műveletek javaslata, jóváhagyása és végrehajtásának naplózása.

Knowledge service: projektmemória-javaslatok, jóváhagyás és szinkronizálás DB/fájl között.

## 5. Coordinator működése

A Coordinator konfigurálható ciklusban (javasolt alapérték: 5–15 perc) ellenőrzi az aktív forrásokat; a ciklus és a várakozás tokentakarékos módját az 5.1 írja le. A forrásokhoz tartozó hitelesítés lehetőleg a VM-en már konfigurált CLI-ket és meglévő bejelentkezési állapotot használja; az AgentHub nem építi újra a hitelesítési rendszereket.

Betölti az aktív források és projekt-hozzárendelési szabályok konfigurációját.

Lekéri az új vagy módosult elemeket és kommenteket. A Trello- és a Jira-adapter elemenként a legutóbbi 10 kommentet hozza (szerző, idő, szöveg), a CLI-adapter a `comments` mezőleképezéssel. A kommentek a ticket leírásának végére kerülnek („Kommentek a forrásban”). Új komment `source_update` eseményt ad, és frissíti a leírást, ha azt közben nem írták át a ticketen.

Normalizálja az eseményeket, rögzíti a külső azonosítókat és a forráshivatkozásokat.

Deduplikál stabil external ID, thread ID és forrásazonosító alapján; szükség esetén szemantikai hasonlóságot használ. A hasonlóságot a `coordinator wait` modell nélkül, szóegyezéssel méri a nyitott ticketekhez. Hasonló ticket esetén az új elemet nem sorolja be szabály alapján, hanem a Coordinator elé teszi a hasonló ticketek listájával. Ha a Coordinator duplikátumnak ítéli, összevonja (`agenthub coordinator merge`): a meglévő ticket megkapja az új elem forrásazonosítóját további forrásként, és ha az új elem új információt hoz, azzal kiegészül a leírása. Az új ticket CANCELLED lesz „összevonva” eseménnyel. Az összevont forráselem későbbi frissítései a meglévő tickethez futnak, de a címét és a leírását nem írják felül. Lezárt (COMPLETED, CANCELLED) tickethez nem lehet összevonni.

Megkeresi a kapcsolódó meglévő ticketet; ha talál, frissíti és eseményt fűz hozzá. A Trello-adapter a kártya listáját és címkéit is hozza (a leírás elején, „[Lista: … · Címkék: …]”). Ha egy meglévő ticket forrásában döntést igénylő változás történik (új komment, válaszlevél a szálban, lista- vagy címkeváltás, tartalmi módosítás), a `coordinator wait` ezt `updates` listában adja át, egyszer. A Coordinator újranyithatja (`reopen`), átsorolhatja (`set`), megjegyzést fűzhet hozzá (`note`), vagy nem tesz semmit (pl. a saját kommentünk visszhangja). Az első indulás előtti frissítéseket nem adja ki.

Új igénynél projektet, típust és prioritást javasol. Bizonytalan projekt esetén NEEDS_TRIAGE állapotba teszi.

Indokolt esetben (látszólag sürgős, halaszthatatlan hiba) bekapcsolhatja a ticket Mehet jelölését, kötelező indoklással (lásd 8.2).

Nem fejleszt és nem módosít projektkódot. Külső írást alapértelmezésben nem végez; a telepítés tulajdonosa a `coordinator_instructions` és a `coordinator_allowed_tools` beállítással engedhet meg konkrét írásokat (pl. válasz kártyára, levélpiszkozat, címkézés). Levelet akkor sem küld, legfeljebb piszkozatot készít.

Rövid operatív összefoglalót rögzít, majd lezárja a ciklust.

A szabályalapú feldolgozás előnyt élvez az AI-hívással szemben. Egyszerű, egyértelmű eseményeknél ne történjen szükségtelen modellhívás.

### 5.1 Ciklus és várakozás modellhívás nélkül

Alapelv: várakozás, polling és determinisztikus feldolgozás nem fogyaszthat tokent. A modell csak akkor kap munkát, ha döntés vagy fejlesztés kell.

A Coordinator és a Worker skill a várakozást egyetlen blokkoló paranccsal végzi: `agenthub coordinator wait`, illetve `agenthub worker wait`. Ez az `agenthub` CLI a VM-en, a session termináljában fut, így a meglévő CLI-bejelentkezéseket használja, de modellt nem hív.

A `coordinator wait` a beállított ütemben lekérdezi a forrásokat, és szabályalapon elvégzi a normalizálást, az external ID / thread ID szerinti deduplikációt, a meglévő ticket frissítését és az egyértelmű projektbesorolást. Csak azokkal az eseményekkel tér vissza, amelyekhez modell kell (pl. bizonytalan besorolás, típus/prioritás javaslat, szemantikus egyezés gyanúja).

A `worker wait` az API-n long-polllal figyeli a saját projekt queue-ját; ha van READY és indításra jelölt (Mehet) ticket, tranzakciósan claimeli, és a ticket tömör összefoglalójával tér vissza. A részletek igény szerint, külön paranccsal kérhetők le (`agenthub ticket show`).

Üres visszatérés esetén a skill azonnal újrahívja a parancsot, egyetlen rövid sorral. Elsődleges mód: háttérben futó parancs, amelynek befejeződése ébreszti a sessiont, így tétlen állapotban nincs modellhívás. Tartalék mód: előtérben blokkoló parancs a Claude Code parancsidőkorlátja alatti maximális várakozással (pl. 10 perc). A ténylegesen támogatott módot az M0 validálja.

A Worker minden ticketet friss kontextusban dolgoz fel (alügynök vagy a Claude Code által dokumentált más mechanizmus), hogy a hosszú ideig futó fő session kontextusa ne nőjön ticketről ticketre.

A Coordinator alapértelmezésben gyors, olcsó modellt használ; a Worker modellje a ticket típusától függ (lásd 14).

## 6. Worker működése

Minden projekthez legfeljebb egy regisztrált Worker-példány tartozik. Egy Worker csak a saját ProjectId-hez tartozó queue-ból vehet fel ticketet. A queue figyelése az 5.1 szerinti `agenthub worker wait` long-pollal történik, modellhívás nélkül.

Induláskor ellenőrzi az AgentHub API elérhetőségét, a projektazonosságot és a konfigurációt.

Feloldja a korábban félbehagyott ticketet, ha van ilyen, az újraindítási szabály alapján.

Ha nincs folytatandó feladat, `agenthub worker wait` paranccsal vár READY és indításra jelölt (Mehet) ticketre a saját projekt queue-jában (lásd 8.2).

A ticketet a `worker wait` tranzakciósan claimeli; a lease-t és a heartbeatet az `agenthub` CLI tartja fenn, nem a modell.

A ticket és projekt kontextusával dolgozik a Claude Code sessionben, a projekt meglévő skilleit és utasításait figyelembe véve.

Az eredményt, módosított fájlok összefoglalóját, teszteket, futási adatokat és eseményeket rögzíti.

Ha emberi információ kell, WAITING_FOR_INPUT állapotba teszi a ticketet és kérdést küld a Dashboardra.

Ha külső írás szükséges, PendingAction javaslatot készít, nem hajtja végre önállóan.

Lezárja vagy megfelelő státuszba helyezi a ticketet, felszabadítja a lease-t.

Egy projekten belül nem lehet két párhuzamosan aktív ticketfeldolgozás. A DB-szintű claim és az egy aktív Worker-példányt biztosító lease/lock együtt védi ezt az invariánst.

## 7. Megszakítás és session-folytatás

Alapértelmezés: a Worker újraindításakor folytassa a félbehagyott ticketet; ez a viselkedés projektenként konfigurálható. A Claude CLI-t azonban a felhasználónak kell kézzel újra elindítania a megfelelő projektkönyvtárban.

A backend tárolja a ticket állapotát, Worker lease-t, utolsó heartbeatet, Claude session ID-t, working directory-t, projekt/ticket kapcsolatot, utolsó ismert állapotot és összefoglalót.

A Worker induláskor megvizsgálja a saját projekt félbehagyott ticketjét.

Ha a Claude Code által támogatott session-resume mechanizmus rendelkezésre áll és a session azonosítható, a Worker skill ezt használja.

Ha a session nem folytatható, a Worker új sessionben folytatja a mentett ticketállapot, összefoglaló, eseménytörténet és projektmemória alapján.

Ha a folytatás nem biztonságos vagy az állapot ellentmondásos, a ticket WAITING_FOR_INPUT vagy NEEDS_TRIAGE állapotba kerülhet; a rendszer nem találgathat.

RDP-kapcsolat megszakadása önmagában ne törölje a ticketet vagy session-metaadatokat.

Validálandó: a Claude Code aktuális verziójának session-azonosítási és resume viselkedése, valamint az, hogy egy skill megbízhatóan képes-e a megfelelő session folytatására. A megvalósítás ne építsen nem dokumentált CLI argumentumra.

## 8. Ticketek és életciklus

| Státusz | Jelentés |
| --- | --- |
| NEW | Újonnan létrehozott ticket |
| READY | Feldolgozásra kész |
| IN_PROGRESS | Worker által claimelt és feldolgozott |
| WAITING_FOR_INPUT | Emberi válaszra vár |
| PENDING_APPROVAL | Jóváhagyásra váró külső művelet vagy döntés |
| BLOCKED | Harmadik félre vár (megrendelő, külső szolgáltató); emberi döntéshez a kérdés (WAITING_FOR_INPUT) való |
| REVIEW | Eredmény ellenőrzésre vár |
| AWAITING_RELEASE | Kész és tesztelt, a következő élesítésre vár |
| COMPLETED | Sikeresen lezárt |
| FAILED | Sikertelen feldolgozás |
| CANCELLED | Megszakított ticket |
| NEEDS_TRIAGE | Projekt vagy értelmezés emberi besorolást igényel |

Minden státuszváltozás TicketEvent rekordot hoz létre. A ticketnek kötelező ProjectId-val rendelkeznie, egy kivétellel: a NEW és a NEEDS_TRIAGE állapotú ticket (a projekt még nem ismert). Globális, projekt nélküli rendszerfeladat az első verzióban nincs: projekt nélkül a ticket nem lehet READY, így Worker sem dolgozhatja fel. NEEDS_TRIAGE állapotból csak ProjectId megadásával lehet továbblépni. A státuszátmeneteket szerveroldali állapotgép validálja; a táblában nem szereplő átmenet tilos.

### 8.0 Megengedett státuszátmenetek

| Honnan | Hová | Ki | Feltétel / megjegyzés |
| --- | --- | --- | --- |
| – | NEW | Coordinator, ember | Ticket létrehozása |
| NEW | READY | Coordinator, ember | Projekt ismert, a ticket végrehajtható |
| NEW | NEEDS_TRIAGE | Coordinator | Projekt vagy értelmezés bizonytalan |
| NEEDS_TRIAGE | READY | ember | ProjectId megadása kötelező |
| READY | IN_PROGRESS | Worker | Tranzakciós claim, csak a saját projekt Workere, és csak ha a ticket Mehet jelölése be van kapcsolva (8.2) |
| IN_PROGRESS | IN_PROGRESS | Worker | Lejárt lease visszanyerése ugyanazon projekt Workere által (eseményként naplózva) |
| IN_PROGRESS | WAITING_FOR_INPUT | Worker | Kérdés létrehozásával együtt |
| IN_PROGRESS | PENDING_APPROVAL | Worker | PendingAction létrehozásával együtt |
| IN_PROGRESS | REVIEW | Worker | Munka kész, eredmény rögzítve |
| IN_PROGRESS | FAILED | Worker, rendszer | Hiba; LastError kitöltve |
| WAITING_FOR_INPUT | READY | rendszer | Minden nyitott kérdésre érkezett válasz |
| PENDING_APPROVAL | READY | rendszer | A PendingAction lezárult (végrehajtva, kézzel megerősítve vagy elutasítva); a Worker folytatja |
| IN_PROGRESS | BLOCKED | Worker | Harmadik félre vár; az ok a LastError mezőbe kerül |
| IN_PROGRESS | AWAITING_RELEASE | Worker | Kész és tesztelt, élesítésre vár (`finish --to release`) |
| IN_PROGRESS | COMPLETED | Worker | Csak ha a projekt nem kér emberi átvételt (`worker.review_required: false`; `finish --to done`) |
| REVIEW | COMPLETED | ember | Eredmény elfogadva |
| REVIEW | READY | ember, Coordinator | Visszaküldés megjegyzéssel, vagy a forrás új bemenetére újranyitás |
| AWAITING_RELEASE | COMPLETED | Worker, ember | Élesítve (`agenthub release done`, lease nélkül) |
| AWAITING_RELEASE | READY | ember, Coordinator | Új visszajelzés élesítés előtt |
| BLOCKED | READY | ember, Coordinator | A blokkoló feloldódott (pl. megjött a megrendelő válasza) |
| COMPLETED | READY | ember, Coordinator | Újranyitás új bemenetre |
| NEW, READY | COMPLETED | Coordinator | Csak válasz kellett, és a telepítés utasítása szerint a Coordinator megadta |
| FAILED | READY | ember, rendszer, Coordinator | Kézi újrapróbálás, automatikus retry a projekt retry-korlátjáig, vagy újranyitás |
| NEW | CANCELLED | Coordinator | Szemantikus duplikátum összevonása (lásd 5), vagy nem munka (pl. automata értesítő; `coordinator dismiss`, indoklással) |
| bármely nem végállapot | CANCELLED | ember | Indoklással; aktív lease felszabadul |

Végállapot: COMPLETED, CANCELLED (a COMPLETED új bemenetre újranyitható). A Worker csak akkor zárhat COMPLETED-re, ha a projekt nem kér emberi átvételt, vagy élesítés után (AWAITING_RELEASE → COMPLETED).

### 8.0.1 Hotfix

A Hotfix jelölés egy szállításra szóló élesítési engedély: a ticketet a Worker elsőként veszi fel, és a projekt utasítása szerint teszt után azonnal élesíti (a kiadás a már tesztelt, élesítésre váró munkát is viheti). Csak ember jelölheti a Dashboardon; a backend Coordinator- és Worker-kulccsal elutasítja. A jelöléssel a ticket Mehet és P1 lesz; a jelölő és az időpont naplózódik. A jelölés lezáráskor (COMPLETED, CANCELLED) és újranyitáskor (REVIEW, AWAITING_RELEASE, COMPLETED, BLOCKED, FAILED → READY) megszűnik.

### 8.1 Ticket mezők

Id, ProjectId, Title, Description, Type, Priority, Status, StartApproved (Mehet, alapértelmezés: false), Hotfix (és ki, mikor jelölte), SourceId, ExternalId, SourceUrl, CreatedAt, UpdatedAt, DueAt (opcionális), AssignedWorkerId, CurrentLeaseId, RetryCount, LastError, ResultSummary, ResultPayload (opcionális), ClaudeSessionId (opcionális).

### 8.2 Indítási jelölés (Mehet)

A ticket alapértelmezésben nem indul el. Minden ticketen van egy Mehet jelölés (StartApproved), amely létrehozáskor kikapcsolt, akár a Coordinator, akár ember hozta létre a ticketet. A Worker csak olyan ticketet claimelhet, amely READY állapotú és a Mehet jelölése be van kapcsolva; jelölés nélküli ticketet nem vesz fel, és nem is kezd rajta előkészítő munkát.

A jelölést ember kapcsolhatja a Dashboardon (a kártyán lévő checkboxszal vagy a ticket részletezőjében). A Coordinator indokolt esetben szintén bekapcsolhatja: ha a ticket látszólag sürgős, halaszthatatlan hibát jelez (pl. éles rendszer leállása, adatvesztés vagy biztonsági kockázat). Ilyenkor az indoklás megadása kötelező; a backend indoklás nélkül elutasítja a kérést. A Dashboard a kártyán megkülönböztetve jelzi a Coordinator által bekapcsolt jelölést, és mutatja az indoklást; az ember bármikor kikapcsolhatja.

A Worker a jelölést nem módosíthatja. A backend a jogosultságot szerveroldalon, a kulcs szerepe alapján ellenőrzi: Dashboard-kulccsal ki- és bekapcsolható, Coordinator-kulccsal csak indoklással bekapcsolható, Worker-kulccsal egyik sem. Minden be- és kikapcsolás TicketEventként naplózódik, a szereplővel és az indoklással együtt.

A jelölés kikapcsolása a már IN_PROGRESS állapotú ticketet nem állítja le; csak a még fel nem vett ticket indítását akadályozza meg. A jelölés megmarad, ha a ticket később visszakerül READY állapotba (pl. válasz, jóváhagyás vagy REVIEW-ból visszaküldés után), így a félbehagyott munka külön újrajelölés nélkül folytatódik.

### 8.3 Ticket események

TicketEvent mezők: Id, TicketId, EventType, OldStatus, NewStatus, ActorType, ActorId, Payload, CreatedAt. Az eseménynapló append-only jellegű; utólagos módosítás helyett korrekciós eseményt kell rögzíteni.

## 9. Emberi közreműködés

A Worker kérdést hozhat létre, ha a feladat nem végrehajtható biztonságos feltételezéssel.

A ticket WAITING_FOR_INPUT állapotba kerül; a kérdés és a válasz külön rekordként tárolódik.

A Dashboard szabad szöveges választ, valamint a Worker által javasolt válasz elfogadását is támogathatja.

A válasz TicketEventként is rögzül; minden szükséges válasz után a ticket READY lesz.

A Worker az eredeti Claude session folytatására törekszik, ha ez támogatott; egyébként a mentett kontextusból indul új session.

## 10. Jóváhagyási modell és külső műveletek

Alapelv: Read → automatikus; Prepare → automatikus; Approve → emberi; Execute → csak jóváhagyás után.

| Művelet | Agent jogosultsága | Emberi lépés |
| --- | --- | --- |
| E-mail | Címzett, tárgy, törzs előkészítése | Jóváhagyás; utána az adapter elküldi, vagy az ember kézzel küldi és MANUALLY_CONFIRMED-et rögzít |
| Jira | Mező-, státusz- vagy kommentjavaslat | Jóváhagyás; utána az adapter végrehajtja, vagy kézi végrehajtás és MANUALLY_CONFIRMED |
| Trello | Kártyaleírás-, komment- vagy listamódosítási javaslat | Jóváhagyás; utána az adapter végrehajtja, vagy kézi végrehajtás és MANUALLY_CONFIRMED |

Jóváhagyás után a végrehajtást az író adapter végzi (APPROVED → EXECUTING → EXECUTED vagy FAILED), ha a célrendszerhez van konfigurált író adapter. Ha nincs, vagy az ember a kézi végrehajtást választja, a művelet MANUALLY_CONFIRMED-del zárul. Az író adapter csak APPROVED állapotú PendingActiont hajthat végre, pontosan a jóváhagyott payloaddal; a jóváhagyás után módosított payload új jóváhagyást igényel. Az EXECUTING állapot a külső hívás előtt rögzül. Ha az AgentHub újraindul, a még el nem indult APPROVED műveletek végrehajtódnak. A félbeszakadt (EXECUTING) és az időtúllépéssel végződött műveletet a rendszer nem küldi újra: FAILED lesz, „bizonytalan” jelöléssel, és az ember ellenőrzi a célrendszerben.

PendingAction mezők: Id, TicketId, ProjectId, ActionType, TargetSystem, TargetReference, ProposedPayload, Status, CreatedBy, CreatedAt, ApprovedAt, ExecutedAt, ExecutionResult. Státuszok: DRAFT, PENDING_APPROVAL, APPROVED, EXECUTING, REJECTED, EXECUTED, MANUALLY_CONFIRMED, FAILED, CANCELLED.

A jóváhagyás szerveroldali állapotellenőrzés, nem promptbeli ígéret. Az agent nem tudhatja saját magát jóváhagyottként beállítani. A CLI-credential és adapter jogosultságok is korlátozzák az írási műveleteket. Ha egy CLI read/write hozzáférést ad együtt, read-only credential vagy szűk wrapper/engedélyezett parancslista szükséges.

## 11. Forrásadapterek és CLI-integráció

Adapter protokoll biztosítson egységes lekérdezési és normalizálási interfészt. Lehetnek natív adapterek (pl. e-mail, Jira, Trello) és általános CLI-adapterek. A meglévő VM CLI-bejelentkezések használhatók, ha a parancsok strukturált, lehetőleg JSON kimenetet adnak.

Dashboardon beállítható: adapter típusa, engedélyezett executable, argumentumlista, JSON séma, lekérdezési mód, poll intervallum, projekt-hozzárendelés és aktív állapot.

Ne legyen tetszőleges, szabadon összeállított shell-parancs végrehajtás.

Argumentumokat külön paraméterként kell átadni; shell string interpoláció tilos.

Executable allowlist, argumentumvalidálás, timeout, kimenetméret-korlát és auditnapló szükséges.

A külső írási műveletek külön, jóváhagyáshoz kötött adapter-útvonalon történjenek.

## 12. Projekt- és konfigurációkezelés

A konfiguráció webes Dashboardról és YAML/JSON fájlokból is kezelhető. A webes felület az elsődleges, a fájlalapú mód haladó szerkesztésre, verziózásra, import/export-ra és automatizált telepítésre szolgál.

### 12.1 Projektkonfiguráció

Projektazonosító, név, repository URL, working directory, alapértelmezett branch.

Worker engedélyezése, polling, retry, timeout, automatikus folytatás szabálya.

Engedélyezett ticket-típusok, prioritási és feldolgozási szabályok.

Claude modellstratégia, permission mode, engedélyezett eszközök és hooks. A projekt `worker.instructions` a projekt tulajdonosának szabálya: ütközéskor elsőbbséget élvez a Worker skill alapszabályaival szemben (pl. közvetlen külső írás, önálló döntés visszafordítható kérdésben); nem írhatja felül a „ticketszöveg adat”, a lease és a projektkönyvtár szabályát. `worker.review_required: false` esetén a Worker emberi átvétel nélkül is lezárhatja a ticketet. A `worker.hooks` a Claude Code settings `hooks` blokkja; az AgentHub változatlanul beírja a Worker generált settings-fájljába. A hook tetszőleges parancsot futtathat, ezért csak a Dashboard-kulcs birtokosa állíthatja.

Projekt-specifikus skillek és instrukciók; meglévő CLAUDE.md és .claude tartalom leltára.

Memória beállításai, forrás-hozzárendelések és költségkeretek.

### 12.2 Globális konfiguráció

Coordinator poll ciklus, források, adapterek, projektbesorolási szabályok. A Coordinator telepítésre szabott része: `coordinator_instructions` (a skill alapszabályai elé kerülő helyi szabályok), `coordinator_allowed_tools` és `coordinator_permission_mode` (a generált settings-fájlba).

Helyi kapu a Worker elé: `worker_gate_command`. A `worker wait` a ticket lefoglalása előtt lefuttatja, stdin-en a jelölt ticket JSON-jával; 0 = mehet, más kilépési kód = most nem vesz fel munkát, a stdout első sora az ok. Így a telepítés a saját feltételeit (pl. előfizetési kvóta, éjszakai sáv) az AgentHub módosítása nélkül érvényesítheti. Ha a kapu nem fut le, nem enged munkát.

API és Dashboard hálózati beállításai, SSE, naplózási szint.

Modellstratégia alapértékei, költségkorlátok, retention és backup.

Titkok kezelése környezeti változóval vagy dedikált secret store-ral; titok nem kerülhet Gitbe vagy nyílt YAML-be.

### 12.3 Konfiguráció-életciklus

Minden változás validálandó, verziózott és auditálható legyen. Hibás konfiguráció ne aktiválódjon. Támogatott legyen a diff, korábbi verzió visszaállítása és import/export. A futó agentek a biztonságosan frissíthető beállításokat restart nélkül vegyék át; a sessiont vagy jogosultságot érintő változásnál jelezze, ha újraindítás szükséges. Újraindítást igényel: a Worker `permission_mode`, `allowed_tools`, `hooks` beállítása, a projekt könyvtára (a többi Worker tiltólistája is), valamint a Coordinator modellje, engedélyezett eszközei és permission mode-ja. Ezekről a mentés után a Dashboard figyelmeztet, a teendővel együtt.

## 13. Projektmemória és tudásmegőrzés

A memória hibrid: strukturált, kereshető rekordok az adatbázisban és olvasható Markdown-fájlok a projektrepositoryban vagy AgentHub által kezelt projektmemória-könyvtárban. A pontos fájlelhelyezés és szinkronizációs szabály a megvalósítás során véglegesítendő.

A meglévő CLAUDE.md, .claude/skills, .claude/commands, MCP-k és dokumentáció leltározása automatikus; módosításuk nem automatikus.

Új tudás javaslatként jelenik meg Knowledge Review felületen; elfogadás, szerkesztés vagy elutasítás szükséges.

Memória tartalmazhat architekturális döntéseket, üzleti szabályokat, projektkonvenciókat, korábbi hibák tanulságait és releváns ticketkapcsolatokat.

Projektmemória kizárólag a saját projekthez érhető el. Projektek között sem adat, sem embedding, sem fájlhivatkozás nem szivároghat át.

AgentHub globális memóriája csak az AgentHub saját működésére vonatkozhat, nem projektüzleti tartalomra.

Meglévő skill vagy CLAUDE.md módosítása előtt diff, backup, emberi jóváhagyás és audit szükséges.

## 14. Claude Code-integráció és modellstratégia

Az AgentHub saját /coordinator és /worker skill-eket biztosít. A konkrét telepítési hely és a Claude Code által felismert skill formátum/névkonvenció a támogatott Claude Code verzióval ellenőrizendő. A meglévő projekt- és felhasználói skillek változatlanul megmaradnak.

Modellstratégia: egyszerű osztályozás/összefoglalás gyors és olcsó modell; közepes vizsgálat közepes modell; komplex fejlesztés és architekturális elemzés erősebb modell. Egyszerű állapotfrissítéshez ne legyen AI-hívás. Legyen konfigurálható alapmodell, feladattípus szerinti modell, automatikus escalation és költségkeret.

A modellváltás és session-resume kizárólag a Claude Code adott verziója által ténylegesen támogatott mechanizmussal történhet. Nem dokumentált kapcsoló, CLI flag vagy belső protokoll nem használható. A megvalósítás indulásakor és minden jelentős Claude Code-verziófrissítésnél kompatibilitási teszt szükséges.

A skill-ek konzolkimenete legyen takarékos: indulás, ellenőrzött források, új ticketek, ciklus vége, aktív ticket, hiba. Promptok, teljes API-válaszok és teljes ticket payloadok csak verbose/debug módban jelenjenek meg.

## 15. Dashboard UX

Kompakt, magas információsűrűségű operatív felület, a rendszer világos/sötét beállítását követve. A vizuális és funkcionális kiindulópont a fejlesztő VM-en jelenleg futó feladatlap-dashboard; az AgentHub Dashboard erre épül, React-re átírva és projektfüggetlenné téve (konkrét projekt-, board- vagy felhőköltség-elemek nélkül). Nem szükséges pixelpontos másolat.

A referencia-dashboardból átvett elemek:

- Felső dobozsor: keret (token-/használati limit), Worker-állapot, Coordinator-állapot, utolsó forrásellenőrzés.
- Kanban oszlopok: Folyamatban, Indítható, Rád vár (kérdés vagy jóváhagyás), Blokkolt, Ellenőrzésre vár; a Kész (24 óra) és az Események oszlop összecsukható.
- Kártyán prioritásállítás, forráslink, relatív időbélyeg; kattintásra részletező párbeszédablak a kérdésekkel és a válaszmezővel.
- Új ticket felvétele párbeszédablakban.
- Worker és Coordinator Leállít/Engedélyez kapcsoló: ez nem process-vezérlés, hanem jelzés, amelyet a skill a következő ciklus elején figyel (a Worker a futó ticketet még befejezi).
- Mehet checkbox minden indítható ticket kártyáján (8.2): a Worker csak a bepipált ticketeket veszi fel. Alapértelmezésben nincs bepipálva. Az Indítható oszlop fejléce mutatja, hány ticket van bepipálva.
- Élő frissítés SSE-vel, kapcsolatvesztés jelzésével.

Felső státuszsáv: Coordinator/Worker állapot, aktív feladat, ticket darabszámok, blocked/approval figyelmeztetések.

Kanban oszlopok státusz szerint, kompakt ticketkártyák prioritásjelöléssel, projekt- és forrásinformációval.

Ticket részletező: leírás, eseménytörténet, futási eredmény, logok, kérdések/válaszok, kapcsolódó műveletek.

Agent nézet: utolsó heartbeat, aktív ticket, Claude session azonosító, modell, futási idő, tokenhasználat és becsült költség.

Approval Center: függő műveletek, javasolt payload, jóváhagyás/elutasítás és manuális végrehajtás visszaigazolása.

Knowledge Review: memóriajavaslatok összehasonlítása, elfogadása, szerkesztése vagy elutasítása.

Konfigurációs felület globális, forrás- és projektszinten; fájl import/export és diff.

Egyprojekt-telepítésben a projektválasztó elrejthető; többprojekt-telepítésben megjelenik.

Finom animációk: kártyamozgás, státuszváltás, hover, toast, skeleton és számlálóátmenet. Kerülendők a felesleges grafikonok és üres adminpanelek.

A Dashboard LAN-ról és Tailscale-en keresztül is elérhető legyen. Alapértelmezésben ne legyen nyilvános internetre kitéve. Megvalósítás: a szolgáltatás minden címen figyel, de az API csak privát (LAN), Tailscale (100.64.0.0/10) és helyi címről érkező kérést szolgál ki; nyilvános címről 403-at ad. Az `AGENTHUB_ALLOW_PUBLIC=1` ezt feloldja (pl. saját reverse proxy mögött), az `AGENTHUB_BIND` pedig egyetlen címre szűkíti a figyelést. Korlát: a szűrés a kliens forráscímére épül, amelyet a Docker alapbeállítása (rootful, iptables) megőriz. Rootless Dockernél vagy IPv6-os port-továbbításnál a kliens a Docker átjárójának címével érkezhet, és a szűrő átengedi; ilyenkor tűzfal vagy `AGENTHUB_BIND` kell.

## 16. API és valós idejű frissítés

REST API a CRUD és parancsjellegű műveletekhez; Server-Sent Events (SSE) az élő ticket-, agent- és dashboard-eseményekhez (a Dashboard írásai REST-en mennek, ezért egyirányú csatorna elég). Minden végpont szerveroldali projekt- és jogosultságellenőrzést végez.

Projects: lista, részletek, konfiguráció, állapot.

Tickets: lista, szűrés, létrehozás, módosítás, claim, státuszátmenet, eseménytörténet.

Agents: regisztráció/metaadat, heartbeat, állapot, session-adatok, futási statisztika.

Sources: adapter-konfiguráció, tesztelés, aktiválás/deaktiválás. Az olvasó adapterek a hoston futnak, a meglévő CLI-bejelentkezésekkel (5.1), ezért egy forrás kipróbálása a hoston történik: `agenthub source test <id>`. A Dashboard a forrás utolsó lekérdezését és hibáját mutatja.

Questions: kérdés létrehozása, válasz beküldése.

PendingActions: javaslat, jóváhagyás, elutasítás, végrehajtási eredmény vagy manuális megerősítés.

Knowledge: javaslat, elfogadás, szerkesztés, elutasítás, szinkronizáció.

Config: validálás, verziók, diff, import/export, rollback.

A pontos OpenAPI-sémát, endpointneveket, request/response modelleket és jogosultsági mátrixot az implementációs tervben kell rögzíteni.

## 17. Adatmodell – fő entitások

| Entitás | Fő mezők / cél |
| --- | --- |
| Installation | Id, Name, Version, CreatedAt; egy VM önálló telepítése |
| Project | Id, Name, Repository, WorkingDirectory, DefaultBranch, ConfigVersion, IsActive |
| AgentInstance | Id, ProjectId (Worker esetén kötelező), Role, Status, LastHeartbeat, SessionId, StartedAt |
| Ticket | Id, ProjectId, Title, Description, Type, Priority, Status, SourceId, ExternalId, SourceUrl, lease és eredménymezők |
| TicketEvent | Id, TicketId, EventType, Actor, Payload, CreatedAt |
| TicketSourceRef | Id, TicketId, SourceId, ExternalId, Url, MergedFromTicketId; összevont további forráselemek |
| Source | Id, Name, AdapterType, Config, IsActive, LastPollAt |
| SourceEvent | Id, SourceId, ExternalId, ThreadId, PayloadHash, ObservedAt, ProcessedAt |
| TicketQuestion | Id, TicketId, Question, SuggestedAnswer, Status, CreatedAt |
| TicketAnswer | Id, QuestionId, Answer, CreatedBy, CreatedAt |
| PendingAction | Id, TicketId, ProjectId, TargetSystem, ProposedPayload, Status, approval/execution mezők |
| ProjectKnowledge | Id, ProjectId, Category, Content, SourceTicketId, Status, FilePath, CreatedAt |
| ConfigVersion | Id, Scope, ScopeId, Version, Config, CreatedBy, CreatedAt |
| AgentRun | Id, AgentInstanceId, TicketId, Model, StartedAt, FinishedAt, TokensIn, TokensOut, EstimatedCost, Outcome |
| AuditEvent | Id, Actor, Action, ResourceType, ResourceId, Payload, CreatedAt |

A tényleges séma normalizálása, indexei, foreign key-k, egyediségkorlátai és adatmegőrzési szabályai Alembic migrációkban legyenek verziózva.

## 18. Biztonság és izoláció

Projektizoláció kötelező: Worker csak saját ProjectId ticketjeit, fájljait, memóriáját és konfigurációját érheti el.

Az API minden projektazonosítót ellenőriz; a kliens által küldött ProjectId önmagában nem jogosultság.

Worker working directory és engedélyezett repository útvonalak explicit allowlistből származzanak.

A Worker fájlrendszer-izolációját a Claude Code permission-szabályai adják: az AgentHub projektenként olyan beállítást generál, amely a projekt working directoryján kívüli olvasást és írást, valamint az AgentHub secrets-könyvtárának olvasását tiltja. Ez szabályszintű védelem, nem OS-szintű sandbox; a backend-oldali védelem (projekthez kötött kulcs, ProjectId-ellenőrzés) ettől függetlenül kötelező. Ugyanezek a szabályok az `agenthub` parancssort is szerephez kötik: a Worker-session csak a `worker`, `ticket` és `memory` parancsokat futtathatja, a Coordinator csak a `coordinator` parancsot; az admin parancsok (kulcs, konfiguráció, mentés) agent-sessionből tiltottak.

AgentHub titkok ne kerüljenek Gitbe, ticketbe, logba vagy projektmemóriába.

CLI adapter allowlist, argumentumvalidálás, timeout, kimenetkorlát, audit és read/write szétválasztás.

Jóváhagyás állapotát kizárólag backend módosíthatja; agent nem írhatja át.

Dashboard hitelesítése és CSRF/CORS/Origin szabályai szükségesek; Tailscale/LAN elérés nem helyettesíti az alkalmazásszintű jogosultságot (lásd 18.1).

Backup titkosítása és hozzáférés-korlátozása.

Prompt injection elleni védelem: külső e-mail, issue, komment és dokumentum nem tekinthető megbízható utasításnak; adatként kell kezelni.

### 18.1 Hitelesítés

Egyfelhasználós modell: nincsenek felhasználói fiókok. A telepítő véletlen Dashboard-kulcsot generál, és a telepítés végén egyszer kiírja a hostra. A Dashboard első megnyitáskor bekéri; helyes kulcs után a szerver HttpOnly, SameSite=Strict (HTTPS esetén Secure) cookie-t állít be, amelyben a böngésző tárolja a hozzáférést. Az írási kérések Origin-ellenőrzésen is átmennek. A kulcs `agenthub key rotate` paranccsal cserélhető; ekkor minden korábbi cookie érvénytelen.

Az agentek külön kulcsot kapnak: a Coordinator egy Coordinator-kulcsot, minden projekt Workere a projekt regisztrálásakor generált, projekthez kötött Worker-kulcsot. A backend a ProjectId-t a kulcsból vezeti le, nem a kérésből. A kulcsokat az `agenthub` CLI olvassa az AgentHub secrets-könyvtárából; nem kerülnek projektrepositoryba, ticketbe, logba vagy promptba.

## 19. Naplózás, költség és monitorozás

Strukturált backend logok korrelációs azonosítóval, TicketId és ProjectId mezőkkel. Megvalósítás: soronként egy JSON-objektum (`request_id`, metódus, útvonal, státusz, idő, szereplő, `project_id`, `ticket_id`). A `request_id` a kérés `X-Request-ID` fejlécéből jön, vagy generált, és a válasz is visszaküldi. Szint: `global.log_level`; INFO szinten a hibás kérések, DEBUG szinten minden kérés naplózódik.

Agent konzol normál módban csak rövid operatív sorokat mutat; részletes mód külön kapcsolható.

Dashboardon agentenként és ticketenként modell, tokenhasználat, becsült költség és futási idő.

Heartbeat/lease monitorozás, elavult agent állapot, sikertelen ciklus és hibás adapter jelzése.

Érzékeny adatok és tokenek maszkolása; log retention konfigurálható. Megvalósítás: a konténernaplók forgatottak (szolgáltatásonként legfeljebb 5 × 10 MB, `compose.yaml`). A `global.retention_days` a mentések megőrzési ideje: az `agenthub backup` az ennél régebbi mentéseket törli. A ticket- és auditesemények append-only jellegűek, ezért nem törlődnek.

A token- és költségadatok csak akkor jelenjenek meg pontosként, ha a Claude Code vagy az integráció megbízhatóan szolgáltatja őket; egyébként becslésként és annak forrásával kell jelölni.

## 20. Hibakezelés és konzisztencia

API/DB átmeneti hiba esetén exponential backoff és korlátozott retry.

Lease lejáratkor a ticket visszanyerhető, de csak az egyetlen projekt-Worker claimelheti újra.

Worker összeomlásakor ticket IN_PROGRESS maradhat, de a stale lease felismerhető legyen.

Ismételt külső események idempotens feldolgozása. Ha egy meglévő külső elem megváltozik, a forrás frissíti a ticket címét, leírását és forrás-URL-jét. Kivétel az a mező, amelyet a ticketen közben átírtak: az megmarad, és a `source_update` esemény jelzi (`kept_local`).

Konfigurációs hiba esetén az utolsó érvényes konfiguráció marad aktív.

DB migráció hibája esetén frissítés megszakítása és backupból visszaállítási útmutató.

Részleges külső művelet eredménye ne legyen EXECUTED, amíg az adapter nem igazolta; kézi végrehajtásnál MANUALLY_CONFIRMED.

## 21. Backup, frissítés és helyreállítás

agenthub backup: PostgreSQL dump, konfiguráció, AgentHub által kezelt memória és szükséges metaadatok mentése.

agenthub update: mentés, main frissítése, függőségek frissítése, DB migráció, health check.

agenthub restore: mentés kiválasztása és visszaállítás ellenőrzött lépésekben.

A frissítés ne módosítsa automatikusan a projekt repositoryk tartalmát.

A frissítés ne indítsa el a Coordinator vagy Worker Claude CLI sessionöket.

A gördülő main/latest csatorna figyelmeztetést és visszaállítási lehetőséget adjon; később stabil release csatorna hozzáadható.

## 22. Tesztelési követelmények

Unit tesztek: állapotgép (minden megengedett és tiltott átmenet a 8.0 tábla szerint), deduplikáció, projekt-hozzárendelés, konfigurációvalidálás, költségszámítás.

Integrációs tesztek: PostgreSQL tranzakciók, ticket claim/lease, heartbeat, API és SSE.

Adapter contract tesztek: JSON parsing, hibás kimenet, timeout, exit code, argumentumkezelés.

Biztonsági tesztek: cross-project hozzáférés, path traversal, shell injection, approval bypass, secret leakage.

Recovery tesztek: Worker leállás IN_PROGRESS ticket közben, lease expiry, session resume siker és fallback.

End-to-end tesztek: Coordinator forráseseményből ticketet készít; Worker feldolgozza; Dashboardon megjelenik az eredmény.

Claude Code kompatibilitási teszt minden támogatott verzióra: skill felismerés, interaktív használat, session resume, modellválasztás.

## 23. Elfogadási kritériumok

Friss Ubuntu VM-en az AgentHub telepíthető a dokumentált GitHub main/latest folyamattal.

A Dashboard/API/DB VM újraindítás után automatikusan elérhető, de Claude agent nem indul el automatikusan.

A Coordinator külön terminálból, kézzel indítható a /coordinator skill használatával.

Projekt Worker külön terminálból, kézzel indítható a /worker skill használatával.

Egy projekthez legfeljebb egy aktív Worker és egy aktív ticketfeldolgozás tartozik.

A Coordinator új forráseseményből ticketet készít, deduplikál, meglévőt frissít és bizonytalan projektet NEEDS_TRIAGE állapotba tesz.

A Worker kizárólag a saját projekt ticketjeit dolgozza fel.

A Worker nem vesz fel olyan ticketet, amelyen a Mehet jelölés nincs bekapcsolva; a jelölést a Worker nem tudja bekapcsolni, a Coordinator csak indoklással.

A Worker kérdést tehet fel, a Dashboardon adott válasz után folytatja a munkát.

A külső írás PendingActionként jelenik meg, és jóváhagyás nélkül nem hajtható végre.

Worker újraindításakor a félbehagyott ticket folytatása alapértelmezésben automatikus, de konfigurálható; a Claude CLI kézi újraindítása kötelező.

A projektmemória DB és Markdown formában is működik, és Knowledge Review jóváhagyást igényel.

A projektmemória és ticket-hozzáférés projektek között izolált.

A Dashboard élő agent/ticket állapotot, eseménytörténetet és költségadatokat mutat, ahol azok rendelkezésre állnak.

Frissítés előtt mentés készül; sikertelen migráció vagy health check esetén dokumentált helyreállítás lehetséges.

## 24. Javasolt megvalósítási fázisok

| Fázis | Tartalom |
| --- | --- |
| 0 – Technikai validáció | Claude Code skill telepítési/felismerési mód, resume, modellváltás; az 5.1 szerinti modellhívás nélküli várakozás; CLI JSON adapter prototípus; permission modell |
| 1 – Alaprendszer | Repo, Docker Compose, FastAPI, PostgreSQL, Alembic, React shell, projektregiszter, config |
| 2 – Ticket core | Ticket állapotgép, event log, deduplikáció, lease/heartbeat, API |
| 3 – Claude skills | /coordinator és /worker skill, kézi indítás, polling, ticket claim, eredményrögzítés |
| 4 – Dashboard | Kanban, ticket részletek, agent státusz, kérdés/válasz, konfiguráció |
| 5 – Integrációk | Első natív forrásadapterek és biztonságos CLI adapter |
| 6 – Approval és memória | PendingAction, Knowledge Review, DB+Markdown szinkronizáció |
| 7 – Üzemeltetés | Install/update/backup/restore, security hardening, recovery és E2E tesztek |

## 25. Nyitott, implementáció előtt validálandó kérdések

Claude Code skill/command jelenlegi hivatalos formátuma, projekt- és user-szintű telepítési helye, valamint a legbiztonságosabb módja annak, hogy az AgentHub saját skilljei ne ütközzenek a meglévőkkel.

Claude Code session resume: pontos parancs/szintaxis, session ID visszakereshetősége, working directory korlátai és sessionök stabilitása.

Claude Code modellváltás: session közben és új session indításakor milyen hivatalos módon állítható.

Modellhívás nélküli várakozás (5.1): támogatja-e a Claude Code háttérparancs befejeződésére ébredést, mekkora az előtérparancs maximális időkorlátja, és mennyi tokent fogyaszt egy üres ciklus.

Ticketenkénti friss kontextus (5.1): alügynök vagy más dokumentált mechanizmus, és annak költsége.

Projektmemória Markdown fájljainak végleges helye és DB-fájl konfliktuskezelési szabálya.

Első körben támogatott forrásadapterek pontos listája és a CLI-k JSON képessége.

Ezek nem blokkolják a specifikációt, de a megfelelő megvalósítási fázisban prototípussal és dokumentációellenőrzéssel kell lezárni őket.

Állapot (2026-11-20): a Claude Code-ra vonatkozó kérdések lezárva, a mérések és döntések a docs/M0-VALIDATION.md-ben. Projektmemória: a Markdown-fájlok helye `~/AgentHub/data/projects/<projekt>/memory/<kategória>.md`; a DB a forrás, a fájl generált (ütközés nincs). Első adapterek: általános CLI (JSON), IMAP, Trello, Jira olvasó; e-mail (SMTP), Trello, Jira író.

## 26. Véglegesített termékdöntések összefoglalója

| Terület | Döntés |
| --- | --- |
| Worker modell | Egy Worker egy projekthez kötődik |
| Párhuzamosság | Projektenként egy Worker, nincs párhuzamos ticketfeldolgozás |
| Memória | Hibrid: adatbázis + Markdown projektfájlok |
| Megszakadt munka | Alapértelmezett automatikus folytatás, konfigurálható |
| CLI újraindítás | A felhasználó kézzel indítja újra a Claude CLI-t |
| Agent indítás | Külön látható terminál, kézi indítás |
| Dashboard/API/DB | Háttérszolgáltatás, Docker Compose |
| Konfiguráció | Webes + YAML/JSON fájl |
| Telepítési csatorna | GitHub main/latest |
| Projektizoláció | VM-en belüli projektek között is szigorú; fájlszinten Claude Code permission-szabályokkal |
| MVP | ROADMAP M1: egy projekt, kézi ticketek, végigjárható; utána azonnal folytatás |
| Várakozás és polling | Modellhívás nélkül, `agenthub ... wait` blokkoló paranccsal (5.1) |
| Projekt nélküli ticket | Csak NEW és NEEDS_TRIAGE állapotban; globális rendszerfeladat az első verzióban nincs |
| Indítás | Ticket alapból nem indul; a Worker csak bepipált (Mehet) ticketet vesz fel. Pipálhat az ember, és indoklással a Coordinator sürgős, halaszthatatlan hibánál |
| Lezárás | Worker REVIEW-ba tesz, COMPLETED csak emberi elfogadással |
| Külső írás | Jóváhagyás után az író adapter végrehajthatja; kézi végrehajtás is választható |
| Hitelesítés | Egyfelhasználós Dashboard-kulcs cookie-ban; projekthez kötött agentkulcsok |
| Dashboard | A meglévő feladatlap-dashboardra épül; élő frissítés SSE-vel |

Dokumentum vége. A specifikáció az eddig egyeztetett követelményeket rögzíti; a validálandó Claude Code-részleteket szándékosan nem kezeli garantált képességként.
