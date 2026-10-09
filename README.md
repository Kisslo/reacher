# reacher
Säljverktyget som automatiserar prospektering och outreach

## Kom igång

Kräver [uv](https://docs.astral.sh/uv/). Python 3.12 hämtas automatiskt.

```sh
uv sync
uv run pytest -q
```

## Lokala API-nycklar

Riktiga källor (SCB, senare Bolagsverket) kräver nycklar. De ligger bara i din egen
`.env`, aldrig i git, config eller loggar (D24):

```sh
cp .env.example .env                 # bash
Copy-Item .env.example .env          # PowerShell
# fyll i nycklarna i .env, kör sedan:
uv run --env-file .env reacher check-sources
```

`check-sources` läser `sources.yaml` och visar vilka nycklar som är satta, aldrig
värdena. Den anropar inga API:er. Vad som hämtas (SNI-koder, kommuner) ändras i
`sources.yaml`, inte i koden.

Finansiella fakta från Bolagsverket (T1-14) hämtas för ringbara aktiebolag i databasen.
Årsredovisningar som redan finns hämtas inte igen:

```sh
uv run --env-file .env reacher fetch-financials
```

På Windows kan Bolagsverket ge `TLS-certifikatet kunde inte verifieras`. Windows saknar
då rotcertifikatet Telia Root CA v2. Öppna https://gw.api.bolagsverket.se i Edge en gång,
eller lägg till i din egen `.env`:

```
SSL_CERT_FILE="C:/Program Files/Git/mingw64/etc/ssl/certs/ca-bundle.crt"
```

Stäng aldrig av certifikatverifieringen: då kan nyckeln avlyssnas.

## Fredagsdemo (J-03)

Hela loopen på testdatan i `tests/fixtures/`: bygg listor, simulera samtal, läs
tillbaka utfallen och visa att en salong som sa "Spärra" vecka 40 saknas vecka 41.
Inga riktiga salonger är inblandade, och simuleringen vägrar köra på något annat än
fixtures.

Kör från repots rot: `build-lists` läser `scoring.yaml` och skriver till `output/`
i den mapp du står i.

```sh
uv run reacher init-db
uv run reacher load-seed
uv run reacher build-lists 2026w40 "Anna,Bengt"
uv run reacher simulate-outcomes output/2026w40_Anna.xlsx output/2026w40_Anna_utfall.xlsx
uv run reacher simulate-outcomes output/2026w40_Bengt.xlsx output/2026w40_Bengt_utfall.xlsx
uv run reacher import-outcomes output/2026w40_Anna_utfall.xlsx
uv run reacher import-outcomes output/2026w40_Bengt_utfall.xlsx
uv run reacher build-lists 2026w41 "Anna,Bengt"
uv run reacher report 2026w40
```

Vad stegen visar:

1. `init-db` och `load-seed` skapar `reacher.db` och läser in fixtures. Ogiltiga
   orgnr och telefonnummer avvisas med en varning.
2. `build-lists` poängsätter de salonger som `callable_salon` släpper igenom och
   skriver en låst xlsx per säljare. Spärrade och NIX-uteslutna salonger är inte med.
3. `simulate-outcomes` fyller i Utfall med seed 42, så resultatet blir samma varje
   gång. Varje fil får minst ett "Spärra" och minst ett "Ej nådd".
4. `import-outcomes` läser tillbaka utfallen. "Spärra" ger en `opt_out`-rad och
   "Registrerad" en `existing_customer`-rad i `suppression`.
5. `build-lists 2026w41` har färre rader: varje företag som sa "Spärra" eller
   "Registrerad" saknas, på alla sina arbetsställen.
6. `report` visar träffgraden för topp 20 mot resten, med urvalsstorlek. Med så här
   få samtal är skillnaden brus, inte ett bevis.

En vecka kan bara byggas en gång per databas, och simuleringen skriver aldrig över en
fil. Börja om genom att ta bort databasen och `output/`:

```sh
rm -rf reacher.db output             # bash
Remove-Item -Recurse reacher.db, output   # PowerShell
```

`tests/test_demo.py` kör exakt samma kommandon i en tom mapp. Ändrar du stegen här,
ändra testet också.
