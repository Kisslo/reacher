-- Råa SCB-koder för compliance (D10, D18). Lagras som TEXT exakt som SCB
-- levererar dem; NULL betyder att källan inte sa något. Koderna tolkas BARA
-- i vyn callable_salon (T1-05), aldrig vid källan eller vid ingest.
--
-- Medvetet INGA CHECK-constraints: en ny eller okänd SCB-kod ska lagras och
-- sedan faila stängt i vyn (som bara släpper igenom uttryckligen tillåtna
-- koder), inte krascha ingest.
--
-- En ALTER TABLE per kolumn: SQLite kan inte lägga till flera kolumner i
-- samma sats.

ALTER TABLE salon ADD COLUMN legal_form TEXT;          -- Juridisk form, "10" = enskild näringsidkare
ALTER TABLE salon ADD COLUMN ftax_status TEXT;         -- F-skattstatus: 0 / 1 / 9
ALTER TABLE salon ADD COLUMN vat_status TEXT;          -- Momsstatus: 0 / 1 / 3 / 9
ALTER TABLE salon ADD COLUMN employer_status TEXT;     -- Arbetsgivarstatus: 0-4 / 9
ALTER TABLE salon ADD COLUMN ad_status TEXT;           -- Reklam på företaget: 11-13 / 21-23
ALTER TABLE salon ADD COLUMN workplace_ad_status TEXT; -- Reklam på arbetsstället, samma koder
