-- T1-05: callable_salon, den enda platsen där det avgörs vem som får ringas.
-- build-lists läser ALLTID härifrån, aldrig från salon direkt (D7, D11).
-- Filtreringen sker vid läsning, så en spärr som importeras efter ingest
-- gäller direkt.
--
-- Fail closed överallt: tillåtna koder listas uttryckligen med IN (...).
-- NULL IN (...) är NULL, och WHERE släpper bara igenom sant, så en saknad
-- eller okänd SCB-kod utesluter salongen i stället för att släppa igenom den.
--
-- s.* så att vyn har samma kolumner som salon. En framtida migration som tar
-- bort eller byter namn på en kolumn som används nedan failar, vilket är
-- avsikten: då måste reglerna här ses över.

CREATE VIEW callable_salon AS
SELECT s.*
FROM salon AS s
WHERE
    -- 1. Spärrlistan: alla skäl, per orgnr, så att alla arbetsställen spärras.
    NOT EXISTS (SELECT 1 FROM suppression AS x WHERE x.orgnr = s.orgnr)

    -- 2. Reklamspärr och telefonspärr/NIX-Telefon, på både företag och
    --    arbetsställe. Bara 11 (tar emot reklam, ingen telefonspärr) är
    --    ringbar. 13 = NIX-Telefon, utesluten tills regel 6.3 är kontrollerad.
    AND s.ad_status IN ('11')
    AND s.workplace_ad_status IN ('11')

    -- 3. NIX 6.3: en möjlig enskild firma måste ha F-skatt, moms eller vara
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
