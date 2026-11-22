# AgentHub – További fejlesztési irányok

**Stratégiai fejlesztési javaslatok · 2026. november 22.**

Ez a dokumentum a jelenlegi AgentHub továbbfejlesztésének lehetséges irányait foglalja össze. Nem új specifikáció és nem kötelező vállalás: kiindulópont a következő milestone-ok és prioritások meghatározásához. A meglévő mérföldkövek állapotát a [ROADMAP.md](ROADMAP.md) tartalmazza.

## Javasolt prioritások

### 1. Megbízható agent-életciklus és helyreállítás

Elsőként az üzemeltetési megbízhatóságot érdemes tovább erősíteni.

- Beragadt vagy váratlanul megszakadt agent sessionök felismerése.
- Lease-ek, heartbeat-ek és ticketállapotok konzisztens rendezése.
- Félbeszakadt munkák biztonságos folytatása vagy visszaadása a queue-ba.
- Egyértelmű recovery folyamat és auditálható eseménytörténet.
- A hibás vagy részlegesen sikerült futásokból való helyreállás tesztelése.

**Cél:** egy megszakadt session ne hagyjon tartósan beragadt ticketet, és ne okozhasson párhuzamos vagy duplikált feldolgozást.

### 2. Megfigyelhetőség és Dashboard

A rendszer működését ne csak az aktuális agentállapotból, hanem a teljes folyamatból lehessen megérteni.

- Agent Timeline és teljes futási előzmény.
- Ticket-életút: forráseseménytől a Coordinator döntésén át a Worker eredményéig.
- Hibák, újrapróbálkozások és várakozási idők elemzése.
- Költség- és tokenfelhasználás projektenként, agentenként és időszakonként.
- Queue-terhelés, feldolgozási idő és projekt-egészségi mutatók.

**Cél:** gyorsan kiderüljön, mi történt, hol akadt el a folyamat, és mennyibe került.

### 3. Intelligensebb Coordinator

A Coordinator fokozatosan válhat egyszerű forrásfigyelőből tudatosabb feladatkoordinátorrá.

- Ticketfüggőségek és blokkoló feladatok kezelése.
- Dinamikus prioritás a sürgősség, határidő és projektterhelés alapján.
- Hasonló vagy ismétlődő hibák felismerése és összevonási javaslat.
- Projektkapacitás és várható feldolgozási idő figyelembevétele.
- A döntések indoklásának és eredetének megőrzése.

**Cél:** a Coordinator ne csak felvegye és besorolja a feladatokat, hanem segítse a helyes végrehajtási sorrend kialakítását is.

### 4. Többszereplős agentmunkafolyamatok

A Worker feladatköre később specializált szerepekkel bővíthető.

Lehetséges szerepek:
- **Developer:** implementáció és hibajavítás.
- **Reviewer:** kódváltozások független ellenőrzése.
- **Tester:** tesztek és regressziós ellenőrzések.
- **Researcher:** vizsgálat, információgyűjtés és technikai javaslat.

Első lépésként szekvenciális munkafolyamat ajánlott: például Developer → Reviewer → szükség esetén Developer javítás → Tester. Ugyanazon kódbázison a párhuzamos módosítás csak később, megfelelő izolációval és konfliktuskezeléssel kerüljön elő.

**Cél:** ellenőrizhető minőségű, több lépésből álló végrehajtás anélkül, hogy a rendszer túl korán bonyolult párhuzamosságot vezetne be.

### 5. Hasznosabb projektmemória

A meglévő DB + Markdown alapú memóriára építve:

- Korábbi ticketekből származó releváns megoldások visszakeresése.
- Architektúra, döntések, konvenciók és ismert hibák strukturált kezelése.
- Kontextushoz illő memória automatikus ajánlása a Workernek.
- Elavult, ellentmondó vagy duplikált tudáselemek felismerése.
- A memória módosításának és archiválásának emberi felülvizsgálata.

**Cél:** a memória ne pusztán tárolja a tudást, hanem a megfelelő pillanatban segítse is a munkát.

### 6. Biztonsági és jogosultsági modell továbbfejlesztése

A meglévő projektizolációra, titokvédelemre és outbound approval folyamatra építve:

- Szerepkör- és projektszintű jogosultságok finomítása.
- MCP-kiszolgálók és külső eszközök allowlistje.
- Fájlrendszer- és parancsvégrehajtási jogosultságok szűkítése.
- Jóváhagyások lejárata, visszavonása és pontos payloadhoz kötése.
- Egységes, kereshető auditnapló az agentek és emberi műveletek számára.

**Cél:** a képességek bővülésével se lazuljon a kontroll az agentek által végrehajtható műveletek felett.

### 7. Külső integrációk bővítése

A már meglévő adapteres megközelítésre építve, igény szerint:

- Bitbucket PR-ek és commitok figyelése, PR-review ticketek létrehozása.
- Review-megállapítások és inline kommentek kezelése, emberi jóváhagyással.
- GitHub, Jira és Trello mélyebb kétirányú integrációja.
- Gmail és Microsoft 365 események és levelezési folyamatok.
- Slack értesítések és emberi visszajelzések.
- CI/CD és monitoring eseményekből automatikus ticketek.

**Cél:** az AgentHub a fejlesztési és üzemeltetési folyamatok közös koordinációs rétege legyen.

## Javasolt stratégiai felépítés

Hosszabb távon három logikai réteg mentén érdemes gondolkodni:

1. **Orchestration** – Coordinator, ticketek, queue, prioritások és agent-életciklus.
2. **Execution** – Claude Code sessionök, Worker és specializált végrehajtó szerepek.
3. **Knowledge & Observability** – projektmemória, audit, futási előzmények és analitika.

Ez egy logikai felosztás, nem feltétlenül külön telepítendő szolgáltatásokat jelent.

## Ajánlott megvalósítási sorrend

1. Agent-életciklus és helyreállítás megerősítése.
2. Teljes futási előzmény és Dashboard-megfigyelhetőség.
3. Coordinator döntési és prioritáskezelési képességeinek bővítése.
4. Szekvenciális Developer → Reviewer → Tester munkafolyamat.
5. Projektmemória relevanciájának és visszakeresésének javítása.
6. Biztonsági modell további szigorítása az új képességekhez igazítva.
7. A tényleges használati igények alapján új integrációk.

## Termékstratégiai ajánlás

A következő verziókban érdemes a Claude Code-ra optimalizált, stabil és jól üzemeltethető rendszert építeni. Az általános, többféle agent-runtime-ot támogató orchestration platform irány hosszabb távú lehetőség, de a túl korai absztrakció növelné a komplexitást, miközben még a jelenlegi működés megbízhatósága és átláthatósága adja a legnagyobb értéket.

A fejlesztési irányok közül mindig a tényleges használat során megjelenő problémák és a mérhető eredmények alapján érdemes választani. A dokumentum nem helyettesíti a ROADMAP mérföldköveit; azokba a kiválasztott, priorizált feladatokat kell később beemelni.
