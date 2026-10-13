-- T1-07: salonger som försvinner ur SCB (D38).
--
-- Varje ingest som sparas är en körning i ingest_run, och varje salong minns
-- den senaste körningen som levererade den (last_seen_run). callable_salon
-- släpper bara igenom salonger från den senaste körningen. En salong som SCB
-- inte längre levererar har troligen stängt, bytt bransch eller flyttat från
-- kommunen, och dess spärrkoder är inte längre uppdaterade: har den gått med i
-- NIX sedan dess vet vi inte om det.
--
-- En rad i ingest_run finns bara för en körning som sparades. Körningen skrivs
-- i samma transaktion som salongerna, så en körning som kraschar halvvägs
-- lämnar varken körning eller ändrade salonger efter sig. Tomma körningar
-- avvisas av ingest (ingest.py). Därför är max(id) alltid en hel körning.
--
-- Salonger raderas aldrig: call_list_row pekar på dem, och en salong som kommer
-- tillbaka ska behålla first_seen_at.

CREATE TABLE ingest_run (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,              -- SalonSource.name: 'csv', 'scb'
    started_at  TEXT NOT NULL,              -- = salon.last_seen_at för körningens salonger
    finished_at TEXT,                       -- sätts sist i samma transaktion
    salons      INTEGER NOT NULL DEFAULT 0, -- salonger som levererades i körningen
    gone        INTEGER NOT NULL DEFAULT 0  -- fanns i förra körningen men inte i den här
);

-- NULL = aldrig sedd i en registrerad körning (t.ex. inläst före migration 009).
-- Sådana salonger är inte ringbara förrän nästa ingest (fail closed).
ALTER TABLE salon ADD COLUMN last_seen_run INTEGER REFERENCES ingest_run(id);

-- Samma vy som i 006, plus regel 6. Tas bort och skapas om så att hela
-- regelverket står på ett ställe.
DROP VIEW callable_salon;

CREATE VIEW callable_salon AS
SELECT s.*
FROM salon AS s
WHERE
    -- 1. Spärrlistan: alla skäl, per orgnr, så att alla arbetsställen spärras.
    NOT EXISTS (SELECT 1 FROM suppression AS x WHERE x.orgnr = s.orgnr)

    -- 2. Reklamspärr och telefonspärr/NIX-Telefon (D28), på både företag och
    --    arbetsställe. Bara 1 är ringbart: tar emot reklam, ingen telefonspärr.
    --    telefonSparrTyp 3 = NIX-Telefon, utesluten som 13 i D20.
    AND s.ad_block_type IN ('1')
    AND s.phone_block_type IN ('1')
    AND s.workplace_ad_block_type IN ('1')
    AND s.workplace_phone_block_type IN ('1')

    -- 3. Bara verksamma företag (D31) och verksamma arbetsställen (D33).
    --    0 = aldrig verksam, 9 = ej längre verksam. Ett verksamt företag kan ha
    --    en nedlagd salong, därför båda.
    AND s.company_status IN ('1')
    AND s.workplace_status IN ('1')

    -- 4. Oskiftade dödsbon ringer vi aldrig (D30). Vyns enda spärrlista.
    --    IS NULL behövs: NULL NOT IN (...) är NULL, och utan den skulle varje
    --    salong utan juridisk form uteslutas här. De fallen avgörs av regel 5 (D22).
    AND (s.legal_form IS NULL OR s.legal_form NOT IN ('91'))

    -- 5. NIX 6.3: en möjlig enskild firma måste ha F-skatt, moms eller vara
    --    registrerad arbetsgivare. Undantaget gäller bara juridiska former som
    --    vi uttryckligen vet är juridiska personer. 10, 99, NULL och varje
    --    okänd kod behandlas som en möjlig enskild firma.
    --    Arbetsgivarstatus 2 (privat arbetsgivare) räknas inte.
    AND (
           s.legal_form IN ('31', '49')  -- 31 = HB/KB, 49 = övriga AB
        OR s.ftax_status IN ('1')
        OR s.vat_status IN ('1', '3')
        OR s.employer_status IN ('1', '3')
    )

    -- 6. Bara salonger som fanns med i den senaste körningen (D38), per
    --    arbetsställe. Finns ingen körning är underfrågan NULL, och = NULL är
    --    aldrig sant: en databas utan körningar har inga ringbara salonger.
    AND s.last_seen_run = (SELECT max(id) FROM ingest_run);
