-- T2-08: vilka signaler salongen hade när listan byggdes, även de med vikt 0
-- (skuggläge, D26), så att T2-07 kan jämföra utfall med och utan en signal.
-- Fryst som reasons och phone: senare ändrade fakta får inte skriva om historiken.
--
-- JSON-array med signalnycklar, t.ex. ["registered_recently", "small_employer"].
-- '[]' = inga signaler. NULL = raden byggdes före T2-08 och vi vet inte, vilket
-- är något annat än inga signaler. Därför ingen NOT NULL DEFAULT '[]'.
ALTER TABLE call_list_row ADD COLUMN signals TEXT;
