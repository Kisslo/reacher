# SCB:s nya API (AFR): endpoints, fält och koder

T1-09 (#46). Underlag för SCB-adaptern (T1-06), de nya compliance-fälten (T1-10) och Team 2:s härledda signaler.

**Källor**
- OpenAPI-kontraktet `https://apiafr.scb.se/swagger/v1/swagger.json`. SCB kallar det "det normativa kontraktet". Typer märkta *kontrakt* kommer därifrån.
- SCB:s Variabelbeskrivning (lokal PDF, gitignorerad): vad koderna betyder.
- Ett riktigt anrop 2026-10-07 med lokal nyckel (`scripts/scb/probe.py`). Typer och format märkta *svar* är kontrollerade där.

Inga riktiga uppgifter finns i den här filen eller i `tests/fixtures/scb/`.

## Anslutning

| | |
|---|---|
| Bas-URL | `https://apiafr.scb.se/v1`. Versionen ligger i sökvägen; v1 gäller tills vidare |
| Nyckel | Header `X-API-Key`. Lokalt i `.env` som `SCB_API_KEY` (D24, T1-11). Utan nyckel: 401 "Invalid or missing API key" |
| Fel | `application/problem+json`: `type`, `title`, `status`, `detail`, `instance`, `traceId`. Vanliga koder: 400, 401, 404, 429, 500, 503 |
| Sidstorlek | `limit` max 5000 |
| Anropsgräns | Inte dokumenterad. Vid överbelastning svarar API:t 429 |
| Driftfönster | Stängt 04:00–04:30 varje natt medan data laddas |
| Senaste uppdatering | `GET /v1/api-info` → `senasteUppdateringsDatum` |

## Endpoints vi använder

API:t har två kontrakt: **JE** (juridisk enhet = företaget, nyckel `peOrgNr`) och **AE** (arbetsställe = salongen, nyckel `cfarNr`). En salong i vår databas är ett arbetsställe.

| Endpoint | Svar | Till |
|---|---|---|
| `GET /v1/arbetsstallen/naringsgren/{kod}?limit=&cursorId=` | `pagination` + `arbetsstallen[]`, AE *partial* | Alla salonger med en viss SNI-kod |
| `GET /v1/arbetsstallen/naringsgren/{kod}/count` | `count`, `path` | Räkna innan vi hämtar |
| `GET /v1/arbetsstallen/{cfarNr}/full` | Ett AE, alla variabler | Telefon och startdatum |
| `GET /v1/juridiskaenheter/{peOrgNr}/full` | Ett JE, alla variabler | Juridisk form, F-skatt, moms, arbetsgivare, företagsstatus, spärrar, namn, registreringsdatum |
| `GET /v1/kodtabeller/{tabell}` | Lista med `kod` + `klartext` | Kodlistorna nedan |

Använd **inte**:
- `/juridiskaenheter/kommun/{kod}`: filtrerar på säteskommun, för en enskild firma ägarens folkbokföringskommun (D19).
- `/arbetsstallen/kommun/{kod}`: alla branscher i kommunen, många gånger fler rader än SNI-filtret.
- `/arbetsstallen/full` och `/juridiskaenheter/full`: hela registret utan filter.

### Inget kombinerat filter

Varje listendpoint filtrerar på en enda sak: näringsgren, kommun, län eller anställdaklass, och för JE även omsättningsklass och juridisk form. "SNI 96210 i kommun 0180" går inte att fråga efter. Adaptern (T1-06) gör därför:

1. En lista per SNI-kod i `sources.yaml`: `/arbetsstallen/naringsgren/{kod}`.
2. Behåll arbetsställen där `belagenhetsadress.kommun` finns i `sources.yaml`. Filtret görs i Python.
3. `/arbetsstallen/{cfarNr}/full` för varje behållen salong.
4. `/juridiskaenheter/{peOrgNr}/full` en gång per unikt orgnr. Flera salonger kan höra till samma företag, så cacha per körning.

Näringsgrensfiltret matchar bara den **primära** näringsgrenen (`rangordning = 1`). En salong med frisör som näringsgren 2 kommer inte med, vilket räcker för MVP:n.

Anropsbudget (*svar*, 2026-10-07): `96210` har 18 415 arbetsställen i hela landet och `96220` 11 327. Med `limit=5000` blir det 4 + 3 = 7 listanrop, plus två anrop per salong i kommunen (steg 3 och 4). Bara bland juridiska personer på första sidan av `96210` låg 695 i 0180.

### Paginering

Listan är sorterad på `peOrgNr` i stigande ordning (*svar*). Alla juridiska personer (`16…`) kommer därför före alla enskilda firmor (`19…`/`20…`). Ett urval av de första sidorna är inte representativt: det innehåller inga enskilda firmor alls.

Cursor, inte offset.
1. Första anropet utan `cursorId`.
2. Svaret har `pagination` med `nextCursorId` (tal), `limit` (tal) och `hasMore` (bool).
3. Nästa anrop: `cursorId=<nextCursorId>` med **samma** `limit`. Upprepa tills `hasMore` är `false`.

Adaptern avgör slutet bara på `hasMore`, aldrig på `nextCursorId`.

## Fältmappning

*Partial* = listanropet, *full* = `/full`-anropet.

| RawSalon | API-fält | Från | Kolumn | Anmärkning |
|---|---|---|---|---|
| `orgnr` | `peOrgNr` | AE partial | `salon.orgnr` | 12 siffror: `16` + orgnr, eller `19`/`20` + personnummer för en enskild firma. Normaliseras vid ingest (T1-04). JE:s `orgNr` (10 siffror) används inte |
| `cfar` | `cfarNr` | AE partial | `salon.cfar` | Tal i JSON, 8 siffror |
| `name` | `ben`, annars `foretagsnamn`, annars `namn` | AE partial, JE full | `salon.name` | Första som inte är tomt efter trim. `ben` = Benämning, salongens namn i dagligt tal. `foretagsnamn` = registrerat företagsnamn (tidigare Firma). `namn` = juridiskt namn, för en enskild firma ägarens personnamn. *Svar:* bara 115 av 5 000 arbetsställen på första sidan har text i `ben`; 2 658 har `""` och 2 227 har `" "` |
| `sni` | `primarNaringsgren.naringsgren` | AE partial | `salon.sni` | Format (*svar*): `"96210"`, fem siffror utan punkt. Fixturerna i `salons.csv` har `96.210` |
| `street` | `belagenhetsadress.bGatuAdress` | AE partial | `salon.street` | Besöksadressen. Aldrig `postAdress`, som också finns på AE (D19) |
| `postal_code` | `belagenhetsadress.bPostNr` | AE partial | `salon.postal_code` | |
| `city` | `belagenhetsadress.bPostOrt` | AE partial | `salon.city` | Ort i Excel (D27) |
| `municipality` | `belagenhetsadress.kommun` | AE partial | `salon.municipality` | Arbetsställets kommun. Aldrig JE:s `kommunSate` (D19) |
| `employee_class` | `anstKl` | AE partial | `salon.employee_class` | Arbetsställets storlek. JE har ett eget `anstKl` för hela företaget |
| `registered_at` | `regDat` | JE full | `salon.registered_at` | När SCB registrerade företaget. Se öppna frågor om `startDat` och "Datum" nedan |
| `phone` | `tel` på AE full, annars `tel` på JE full | AE full, JE full | `contact` (`kind='phone'`) | Arbetsställets nummer först (som T1-08). *Svar:* tomt (`""`) på båda nivåerna i hela stickprovet, så formatet är okänt. Se "Flaggat". E.164 vid ingest |
| `website` | finns inte | | | Se "Flaggat" |
| `legal_form` | `jurform` | JE full | `salon.legal_form` | |
| `ftax_status` | `fSkattStat` | JE full | `salon.ftax_status` | |
| `vat_status` | `momsStat` | JE full | `salon.vat_status` | |
| `employer_status` | `arbGivStat` | JE full | `salon.employer_status` | |
| `ad_status` | finns inte | | `salon.ad_status` | Se "Flaggat" |
| `workplace_ad_status` | finns inte | | `salon.workplace_ad_status` | Se "Flaggat" |
| *ny i T1-10* | `ftgStat` | JE full | `company_status` | D31 |
| *ny i T1-10* | `reklamSparrTyp` | JE full | `ad_block_type` | D28 |
| *ny i T1-10* | `telefonSparrTyp` | JE full | `phone_block_type` | D28 |
| *ny i T1-10* | `reklamSparrTyp` | AE partial | `workplace_ad_block_type` | D28. Spärrarna finns på **både** företag och arbetsställe |
| *ny i T1-10* | `telefonSparrTyp` | AE partial | `workplace_phone_block_type` | D28 |

Hämtas men lagras inte: `epost` och `epostSparrTyp` (dataminimering, Format 1), `postAdress`, `kommunSate`, `lanSate`, `geografiskInformation` (koordinater, för en enskild firma ibland en bostad), `agKat`, `sektor`, `privPubl`, `expImp`, `hjVerksJE`, `aeTyp`, `slutDat`.

## Kodfält: JSON-typ och koder

API:t blandar strängar och tal, och samma fält kan ha olika typ i olika endpoints: `anstKl` och `aeStat` är strängar i AE partial men tal i AE full, och kodtabellerna har ofta tal där datan har strängar. Adaptern konverterar därför varje kod med `str()` och inget annat (D18), så att `1` och `"1"` båda blir `"1"`.

| Fält | Nivå | Kontrakt (data) | Kontrakt (kodtabell) | Svar | Koder |
|---|---|---|---|---|---|
| `jurform` | JE | string | string | som kontraktet | 2 siffror. `10` fysisk person (enskild firma), `31` HB/KB, `49` övriga AB, `91` oskiftat dödsbo, `99` ej utredd. Hela listan: Variabelbeskrivningen eller `kodtabeller/jurformkoder` |
| `fSkattStat` | JE | string | number | som kontraktet | `0` aldrig, `1` registrerad, `9` avregistrerad |
| `momsStat` | JE | string | number | som kontraktet | `0` aldrig, `1` registrerad, `3` via representant, `9` avregistrerad |
| `arbGivStat` | JE | string | number | som kontraktet | `0` aldrig, `1` vanlig, `2` privatarbetsgivare, `3` via representant, `4` ambassad/konsulat, `9` avregistrerad |
| `ftgStat` | JE | string | string | som kontraktet | `0` aldrig verksam, `1` verksam, `9` ej längre verksam |
| `reklamSparrTyp` | JE, AE | number | number | som kontraktet | `1` tar emot reklam, `2` har frånsagt sig reklam (*svar*, kodtabellen) |
| `telefonSparrTyp` | JE, AE | number | number | som kontraktet | `1` ej telefonnummerspärrat, `2` telefonnummerspärr telemarketing, `3` NIX-Telefon (*svar*, kodtabellen) |
| `aeStat` | AE | string (partial), number (full) | number | som kontraktet | `0` aldrig verksam, `1` verksam, `9` ej längre verksam |
| `anstKl` | JE, AE | string (JE, AE partial), number (AE full) | number | som kontraktet | Se nästa avsnitt |
| `omsKl` | JE | string | number | som kontraktet | Se "Omsättning" |
| `bolStat` | JE | string | number | som kontraktet | Se "Status hos Bolagsverket" |
| `naringsgren` | AE, JE | string | string | som kontraktet | `96210` frisörer och barberare, `96220` skönhetsvård och andra skönhetsbehandlingar (SNI 2025, avdelning `T`) |

## Svar till Team 2: vilken skala är `anstKl`?

**Storleksklass Anställda** (*svar*, `kodtabeller/anstklkoder`): `0` uppgift saknas, `1` 0 anställda, `2` 1–4, `3` 5–9, `4` 10–19, `5` 20–49 och uppåt till `16` (10 000 eller fler). Inte SME-skalan. `small_employer_classes: ["2"]` i `scoring.yaml` stämmer, ingen ändring.

## Omsättning

**Ja.** JE full har `omsKl` (Storleksklass Omsättning) och `omsAr` (vilket år, tal). Den räknas fram ur momsredovisningen och finns därför även för enskilda firmor som är momsregistrerade. Klasser: `0` < 1 tkr, `1` 1–499 tkr, `2` 500–999 tkr, `3` 1 000–4 999 tkr, `4` 5 000–9 999 tkr, `5` 10 000–19 999 tkr, `6` 20 000–49 999 tkr, `7` 50 000–99 999 tkr, `8` 100 000–499 999 tkr, `9` 500 000–999 999 tkr, `10` 1 000 000–4 999 999 tkr, `11` 5 000 000–9 999 999 tkr, `12` > 9 999 999 tkr.

Det finns också ett filter, `/juridiskaenheter/omsattningsklass/{kod}`. Variabelbeskrivningens finare skala (Storleksklass Fin) finns inte i API:t. Om `omsKl` ska användas för `low_revenue`, eller som filter före Bolagsverket, är Team 2:s beslut (öppen fråga i `claude.md`).

## Status hos Bolagsverket

**Ja.** JE full har `bolStat`: `0` normalläge, `11`–`13` ackord, `20`–`24` konkurs, `31`–`35` likvidation, `36`–`78` avförd, fusion, ombildning m.m., `80`–`82` företagsrekonstruktion, `85`–`87` resolution, `90`–`99` delning och övertagande. Hela listan: Variabelbeskrivningen eller `kodtabeller/bolstatkoder`. Enheter som inte registreras hos Bolagsverket, bland annat alla enskilda firmor, har alltid `0`. Används inte i dag, se öppna frågor.

**Företagsstatus** (`ftgStat`) är bekräftad på JE, i både partial och full. Arbetsstället har en egen status, `aeStat`.

## Flaggat: fält vi använder i dag som saknas i nya API:t

1. **Webbadress.** Varken kontraktet eller Variabelbeskrivningen har något webbfält, men D9 säger att API:t har det. Från SCB blir `website` alltid `None`, och då skapar ingest ingen `contact`-rad. Ingen kodändring behövs.
2. **Den tvåsiffriga Reklam-koden** (`ad_status`, `workplace_ad_status`, koderna 11–23 i D20) finns inte. Den är ersatt av `reklamSparrTyp` + `telefonSparrTyp` på båda nivåerna (D28). Dagens vy kräver `ad_status IN ('11')`, så en SCB-salong utan de gamla kolumnerna utesluts alltid. T1-10 måste landa före eller tillsammans med T1-06, och bestämmer vad som händer med de gamla kolumnerna.
3. **`area`** är redan borttagen (D19). Inget att göra.
4. **Telefonnummer kan saknas helt.** I stickprovet (två företag och deras salonger) var `tel` tomt på både AE och JE. Gäller det de flesta salonger har vi inget nummer att ringa, och D9 (telefon från SCB) håller inte. T1-06 mäter andelen med telefon före J-04.

## Öppna frågor från T1-09 (inte beslutade)

- **Arbetsställets status (`aeStat`).** D31 tittar bara på företaget, så en nedlagd salong (`aeStat` `9`) hos ett verksamt företag släpps igenom. Förslag: lagra `aeStat` rått och kräva `'1'` i `callable_salon`, som D31. Beslut i T1-10.
- **Konkurs och likvidation (`bolStat`).** Förslag: ingen regel i MVP:n, eftersom `ftgStat` redan fångar företag som inte längre är verksamma. Ta upp igen efter första riktiga listan.
- **`registered_at`:** `regDat` (företaget registrerades) eller AE `startDat` (salongen blev verksam)? Redan en öppen fråga för Team 2. Mappningen ovan behåller dagens betydelse.
- **Ägarens namn i Excel.** För en enskild firma utan `ben` och `foretagsnamn` blir `name` ägarens personnamn. *Svar:* 4 885 av 5 000 arbetsställen på första sidan saknar `ben` (tomt eller ett mellanslag). Den enskilda firman i stickprovet hade inget `foretagsnamn`, så den skulle visas med ägarens namn.

## Att tänka på i T1-06

- **`str()`, men inte på `None`.** `str(None)` är `"None"`. Vyn utesluter det (fail closed), men det bryter regeln att NULL betyder "källan sa ingenting". Skriv `None if v is None else str(v)`.
- **Tomt är `""`, inte `null`.** Stickprovet har inga `null` alls. Saknade värden kommer som `""` eller `" "` (`ben`, `tel`, `epost`, `coAdress`, `foretagsnamn`). Trimma och gör om tomma strängar till `None` i adaptern, så att NULL fortsätter betyda "källan sa ingenting" (D18). Annars väljer namnkedjan `" "`, och ingest avvisar salongen för att den saknar namn.
- **Datum** kommer som `ÅÅÅÅ-MM-DDTtt:mm:ss.sssZ` i UTC (*svar*). Ett datum utan klockslag skickas som svensk midnatt, t.ex. `2025-02-02T23:00:00.000Z` för 3 februari (`T22:00` sommartid). Andra är riktiga tidpunkter (`T17:02:00.000Z`). Datumdelen av strängen är alltså ofta en dag för tidig: konvertera till `Europe/Stockholm` och ta datumet. På Windows kräver `zoneinfo` paketet `tzdata`, ett nytt beroende som beslutas i T1-06.
- **`slutDat`** saknas helt på AE när det inte finns. På JE kan det vara ett gammalt datum för ett verksamt företag. Använd det aldrig för att avgöra status: det gör `ftgStat` och `aeStat`.
- **`cfarNr` är ett tal.** `str()` innan det blir `cfar`, eftersom dedupe-nyckeln är text.
- **Logga aldrig** `peOrgNr`, namn, adress eller telefon. För en enskild firma är `peOrgNr` ett personnummer. Logga antal.
- **Fel:** 429, 500 och 503 ger backoff och nytt försök (tenacity). 401 avbryter direkt, för då är nyckeln fel. Kör inte 04:00–04:30.
- **Samma `limit`** genom hela pagineringen.

## Inspelade svar (`tests/fixtures/scb/`)

Formen är kopierad från riktiga svar (2026-10-07), värdena är påhittade. Formen kontrollerades med `scripts/scb/probe.py` och `scripts/scb/compare.py`. `tests/test_scb_fixtures.py` låser formen och kontrollerar att inget ser riktigt ut.

| Fil | Anrop | Innehåll |
|---|---|---|
| `ae_naringsgren_96210.json` | `/arbetsstallen/naringsgren/96210?limit=3` | Första sidan (`hasMore: true`). Två arbetsställen hos samma AB, ett i 0180 och ett i Solna (0184) som adaptern ska filtrera bort, plus en enskild firma vars `ben` är ett mellanslag |
| `ae_full.json` | `/arbetsstallen/90000003/full` | Den enskilda firmans salong. Inget telefonnummer, inget `slutDat`, startdatum som svensk midnatt |
| `je_full.json` | `/juridiskaenheter/198013012342/full` | Den enskilda firman, utan `foretagsnamn`. Hemadressen och säteskommunen (Nacka, 0182) skiljer sig från salongen (D19) |

Anonymisering:
- Orgnr är Luhn-giltiga men påhittade. Personnumret har månad 13, så det kan aldrig tillhöra någon.
- Telefonnummer från PTS:s serier för fiktion (samma som `salons.csv`). E-post är tom, som i riktiga svaren.
- Namn, gator och koordinater är påhittade. Postnummer, kommun- och länskoder och kodvärden får vara riktiga: de pekar inte ut någon.
- Formen är exakt som i det riktiga svaret: fältnamn, ordning, tomma strängar i stället för `null`, saknade fält, JSON-typer och datumformat. Telefonformatet är okänt eftersom stickprovet saknade nummer.
