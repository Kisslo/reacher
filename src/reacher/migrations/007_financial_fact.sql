-- T1-12: finansiella fakta ur årsredovisningar (D25, D26, D27).
--
-- Per företag och räkenskapsår, inte per arbetsställe: Kedjan Klipp har fyra
-- salonger men en årsredovisning. Därför ingen främmande nyckel till salon,
-- salon.orgnr är inte unikt.
--
-- Nyckel/värde i stället för en kolumn per fält: ett nytt fält ur
-- årsredovisningen är en ny rad i bolagsverket.tag_map (sources.yaml), ingen
-- migration (D29). Därför inget CHECK på key.
--
-- Saknat värde = ingen rad, aldrig 0 (D25). NOT NULL på value gör att en
-- trasig källa kraschar i stället för att tyst lagra "inget" som något.

CREATE TABLE financial_fact (
    orgnr           TEXT NOT NULL,     -- 10 siffror, normaliserat som salon.orgnr
    period_end      TEXT NOT NULL,     -- räkenskapsårets slut, ÅÅÅÅ-MM-DD
    key             TEXT NOT NULL,     -- 'revenue', 'net_result', ... (tag_map)
    -- Hela kronor som rapporterat, tecknet behålls. typeof-kontrollen stoppar
    -- 1234567.5 och '1 234 567': SQLite sparar annars det mesta i en INTEGER-kolumn.
    value           INTEGER NOT NULL CHECK (typeof(value) = 'integer'),
    source_document TEXT,              -- Bolagsverkets dokument-id; en rapport täcker ofta två år
    fetched_at      TEXT NOT NULL,     -- när värdet senast ändrades, inte senast det sågs
    PRIMARY KEY (orgnr, period_end, key)
);

-- Team 2:s ingång (T2-09, J-06): de senaste räkenskapsåren per företag.
--
-- Rangordnat per orgnr, inte per (orgnr, key). Saknar 2024 års rapport
-- net_result ska Resultat inte visa 2023 bredvid Omsättning 2024, och
-- loss_making ska inte räkna på ett äldre år än det senaste: rang 1 är
-- företagets senaste räkenskapsår, och saknas nyckeln där finns ingen rad.
--
-- DENSE_RANK så att alla nycklar för samma år får samma rang.
-- 3 = bolagsverket.years i sources.yaml (D27). Ändras det ena, ändra det andra
-- i en ny migration; test_latest_view_keeps_as_many_years_as_sources_yaml
-- failar annars.
CREATE VIEW latest_financial_fact AS
SELECT orgnr, period_end, key, value, source_document, fiscal_year_rank
FROM (
    SELECT
        f.*,
        DENSE_RANK() OVER (PARTITION BY f.orgnr ORDER BY f.period_end DESC) AS fiscal_year_rank
    FROM financial_fact AS f
)
WHERE fiscal_year_rank <= 3;
