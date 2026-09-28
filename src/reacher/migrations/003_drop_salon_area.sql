-- SCB har ingen stadsdelsvariabel (Variabelbeskrivning: bara Besöks-/PostOrt,
-- Kommun, Län, TatSmaTyp och koordinater). "Område" i Excel fylls från city
-- (BesöksPostOrt). Kräver SQLite 3.35+, vi kör 3.53.
ALTER TABLE salon DROP COLUMN area;