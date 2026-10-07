# Fixtures (T1-03, #12)

Påhittad testdata i samma form som SCB levererar. Används av `reacher load-seed`,
av testerna för `callable_salon` (T1-05) och av Team 2 tills riktig data finns.

| Fil | Innehåll |
|---|---|
| `salons.csv` | En rad per salong, kolumnerna i `CSV_COLUMNS` (`sources/base.py`). Rå och stökig med flit: normalisering sker vid ingest (T1-04). |
| `signals.csv` | Bara rubrikrad. Evidensbaserade signaler är parkerade (D4). |
| `ground_truth.csv` | `has_empty_chairs` per normaliserat orgnr + cfar. **Läses BARA av simuleringen (T2-05), aldrig av ingest eller poängsättning.** |
| `scb/` | Inspelade, anonymiserade svar från SCB:s nya API (T1-09). Beskrivs i `docs/scb-fields.md`. Läses av adapter-testerna (T1-06). |

## Regler

- **Allt är påhittat.** Orgnr och personnummer har giltig kontrollsiffra men är
  uppdiktade. Telefonnummer ligger i PTS:s serier för fiktion
  (070-174 06 05–99, 08-465 004 00–99). Lägg aldrig in riktiga uppgifter här.
- **Redigera inte i Excel.** Det byter till semikolon och ANSI och tappar
  inledande nollor. Använd en texteditor.
- **Datumgränser åldras.** "24 månader" är räknat från 2026-09-28. Tester som
  beror på `registered_recently` måste använda ett fast "idag".
- **Ta inte bort kantfall.** `tests/test_fixtures.py` kontrollerar att varje fall
  i tabellen nedan finns kvar.

## Antaganden (verifieras i T1-08, #11)

- `sni` som `96.210` / `96.220` (SNI 2025, femsiffrigt). Ingen regel läser
  fältet än, så ett annat format betyder bara att filen genereras om.
- `city` = BesöksPostOrt, `municipality` = fyrsiffrig kommunkod (D19).
Se `docs/scb-fields.md`. SNI-koderna är bekräftade (`96210`, `96220`). Formatet och resten verifieras mot ett riktigt svar i T1-06 (#22).

## Kantfall

"Förväntat" nedan följer de föreslagna standardvalen för T1-05 (se Open questions
i `claude.md`). Raderna märkta "beslut N" ändrar utfall om beslutet blir ett annat.
Rad = radnummer i `salons.csv` (rubriken är rad 1). Övriga rader är vanliga,
ringbara salonger med varierade signaler.

| Rad | Namn | Varför raden finns |
|---|---|---|
| 2 | Kedjan Klipp Söder | Samma orgnr, 3 cfar (1/3). Orgnr med bindestreck |
| 3 | Kedjan Klipp City | Samma orgnr, 3 cfar (2/3). Orgnr 12 siffror med 16 |
| 4 | Kedjan Klipp Solna | Samma orgnr, 3 cfar (3/3). Bara ARBETSSTÄLLET har reklamspärr |
| 5 | Salong Dubbelgångaren | Dubblett (1/2): orgnr 10 siffror |
| 6 | Salong Dubbelgångaren | Dubblett (2/2): samma salong, orgnr 16+12 siffror, telefon i annat format |
| 7 | Hårateljén | Utan cfar (1/2): måste dedupliceras via IFNULL |
| 8 | Hårateljén | Utan cfar (2/2): samma orgnr igen, ska bli EN rad |
| 9 | Studio Linnea | Enskild firma, PeOrgNr 19+ÅÅMMDDNNNN med bindestreck |
| 10 | Barberare Nord | Enskild firma, PeOrgNr 20+... utan bindestreck |
| 11 | Felaktiga AB | OGILTIGT orgnr: fel kontrollsiffra -> avvisas |
| 12 | För Korta AB | OGILTIGT orgnr: 9 siffror -> avvisas |
| 13 | Namnlösa Salongen | SAKNAR orgnr -> avvisas (kan inte spärras) |
| 14 | Tysta Saxen | Telefon saknas |
| 15 | Klippet | Telefon ogiltig (för kort) |
| 16 | Lockigt | Telefon ogiltig (bokstäver) |
| 17 | Frisyr & Form | Fast telefon 08, med mellanslag |
| 18 | Klipp, Färg & "Form" Åkersberga | Namn med komma, citattecken och åäö (testar CSV-citering) |
| 19 | Syskonen Sax HB | HB (31), allt okänt -> NIX-regeln gäller INTE |
| 20 | Okända Salongen AB | AB (49), allt okänt, ad 11 -> NIX-regeln gäller INTE, ska med |
| 21 | Nollställda AB | AB (49), allt 0 -> fortfarande inte enskild firma, ska med |
| 22 | Oklara Salongen | Juridisk form 99 (ej fastställd), allt 0 -> behandlas som enskild firma (beslut 3) |
| 23 | Oklara Men Seriösa | Juridisk form 99 med F-skatt -> ringbar |
| 24 | Mystiska Salongen | Juridisk form tom, allt okänt -> behandlas som enskild firma, exkluderas |
| 25 | Salong Solo 1 | Enskild firma: Bara F-skatt -> ringbar |
| 26 | Salong Solo 2 | Enskild firma: Bara moms -> ringbar |
| 27 | Salong Solo 3 | Enskild firma: Bara moms via ombud (3) -> ringbar |
| 28 | Salong Solo 4 | Enskild firma: Bara arbetsgivare -> ringbar |
| 29 | Salong Solo 5 | Enskild firma: Bara arbetsgivare via ombud (3) -> ringbar |
| 30 | Salong Solo 6 | Enskild firma: Privat arbetsgivare (2) räknas inte -> exkluderas (beslut 2) |
| 31 | Salong Solo 7 | Enskild firma: Aldrig registrerad på något -> exkluderas |
| 32 | Salong Solo 8 | Enskild firma: Allt avregistrerat (9) -> exkluderas |
| 33 | Salong Solo 9 | Enskild firma: Allt okänt -> exkluderas (fail closed) |
| 34 | Salong Solo 10 | Enskild firma: F-skatt + resten okänt -> ringbar |
| 35 | Salong Solo 11 | Enskild firma: Okänt/avregistrerat/privat -> exkluderas |
| 36 | Salong Solo 12 | Enskild firma: F-skatt avregistrerad men moms -> ringbar |
| 37 | Salong Solo 13 | Enskild firma: Allt registrerat -> ringbar |
| 38 | Salong Solo 14 | Enskild firma: Avregistrerad F-skatt, ingen moms, okänd arbetsgivare -> exkluderas |
| 39 | Reklamtest 12 | ad_status 12 på företaget -> exkluderas |
| 40 | Reklamtest 13 | ad_status 13 på företaget -> exkluderas (beslut 1) |
| 41 | Reklamtest 21 | ad_status 21 på företaget -> exkluderas |
| 42 | Reklamtest 22 | ad_status 22 på företaget -> exkluderas |
| 43 | Reklamtest 23 | ad_status 23 på företaget -> exkluderas |
| 44 | Reklamtest tom | ad_status tom på företaget -> exkluderas |
| 45 | Arbetsställe Okänt | workplace_ad_status tom, företaget 11 -> exkluderas (fail closed) |
| 46 | Arbetsställe Spärrat | Bara arbetsstället spärrat (22) -> exkluderas |
| 47 | Nystartade Drömsalongen | SPÄRRAD MEN HÖGT POÄNG: ny (2025), 1-4 anställda, ad 23 -> exkluderas |
| 48 | Gränsfallet Inom | Registrerad 2024-09-29: precis INOM 24 mån (per 2026-09-28) |
| 49 | Gränsfallet Utanför | Registrerad 2024-09-27: precis UTANFÖR 24 mån (per 2026-09-28) |
| 50 | Datumlösa Salongen | Registreringsdatum saknas |
| 51 | Storleksokända | Storleksklass 0 = UPPGIFT SAKNAS, inte 0 anställda |
| 52 | Ensamma Saxen | Storleksklass 1 = 0 anställda |
| 53 | Tomma Klassen | Storleksklass tom |
| 54 | Stora Salongen | Storleksklass 4 = 10-19 anställda |
