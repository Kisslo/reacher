-- T1-10: compliance-fälten från SCB:s nya API (D28, D30, D31, D32, D33).
--
-- Den gamla tvåsiffriga Reklam-koden (ad_status, workplace_ad_status, D20)
-- finns inte i nya API:t. Den ersätts av reklamSparrTyp + telefonSparrTyp på
-- både företag (JE) och arbetsställe (AE), och ftgStat lagras som
-- company_status och aeStat som workplace_status. Gamla kolumnerna tas bort (D32): en regel per spärr, och
-- fixturerna har samma form som SCB-svaren.
--
-- Råa koder som TEXT, NULL = källan sa ingenting, inga CHECK (D18).
--
-- SQLite vägrar DROP COLUMN på en kolumn som en vy använder, så vyn tas bort
-- först och skapas om sist. Ligger allt i samma fil kan ingen glömma det.

DROP VIEW callable_salon;

ALTER TABLE salon DROP COLUMN ad_status;
ALTER TABLE salon DROP COLUMN workplace_ad_status;

ALTER TABLE salon ADD COLUMN company_status TEXT;             -- ftgStat (JE): 0 / 1 / 9
ALTER TABLE salon ADD COLUMN workplace_status TEXT;           -- aeStat (AE): 0 / 1 / 9
ALTER TABLE salon ADD COLUMN ad_block_type TEXT;              -- reklamSparrTyp (JE): 1 / 2
ALTER TABLE salon ADD COLUMN phone_block_type TEXT;           -- telefonSparrTyp (JE): 1 / 2 / 3
ALTER TABLE salon ADD COLUMN workplace_ad_block_type TEXT;    -- reklamSparrTyp (AE)
ALTER TABLE salon ADD COLUMN workplace_phone_block_type TEXT; -- telefonSparrTyp (AE)

-- Samma upplägg som 004: fail closed, tillåtna koder listas med IN (...).
-- NULL IN (...) är NULL och WHERE släpper bara igenom sant.
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
    );
