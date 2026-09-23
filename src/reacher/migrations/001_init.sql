-- Datum och tidsstämplar lagras som ISO-8601 TEXT. SQLite har ingen datumtyp.

CREATE TABLE salon (
    id             INTEGER PRIMARY KEY,
    orgnr          TEXT NOT NULL,
    cfar           TEXT,                 -- arbetsställenummer; en firma kan ha flera salonger
    name           TEXT NOT NULL,
    sni            TEXT,                 -- 96.21 / 96.22
    street         TEXT,
    postal_code    TEXT,
    city           TEXT,
    municipality   TEXT,
    area           TEXT,                 -- stadsdel, det som hamnar i kolumnen "Område"
    employee_class TEXT,                 -- SCB storleksklass, t.ex. "1-4"
    registered_at  TEXT,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL
);

-- OBS: UNIQUE(orgnr, cfar) funkar INTE som dedupe-nyckel i SQLite, eftersom
-- NULL aldrig är lika med NULL - två rader med samma orgnr och cfar=NULL
-- skulle båda få plats. IFNULL i ett uttrycks-index löser det.
CREATE UNIQUE INDEX salon_identity ON salon (orgnr, IFNULL(cfar, ''));

CREATE TABLE contact (
    id         INTEGER PRIMARY KEY,
    salon_id   INTEGER NOT NULL REFERENCES salon(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('phone', 'website', 'email')),
    value      TEXT NOT NULL,            -- telefon normaliserad till E.164
    source_url TEXT,                     -- hamnar i kolumnen "Källa"
    found_at   TEXT NOT NULL,
    confidence REAL,
    UNIQUE (salon_id, kind, value)
);

CREATE TABLE signal (
    id          INTEGER PRIMARY KEY,
    salon_id    INTEGER NOT NULL REFERENCES salon(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,           -- fritt, inget CHECK: signaler kommer och går
    value       REAL NOT NULL DEFAULT 1.0,
    evidence    TEXT,                    -- citatet från sidan, ordagrant
    source_url  TEXT,
    observed_at TEXT NOT NULL
);
CREATE UNIQUE INDEX signal_identity
    ON signal (salon_id, key, IFNULL(source_url, ''));
CREATE INDEX signal_by_salon ON signal (salon_id);

-- Spärrlistan. En rad per (orgnr, skäl), så att skälen inte skriver över varandra:
-- en kund som dessutom säger "Spärra" har två rader, och när kundraden tas bort
-- ligger opt_out kvar. En salong är spärrad om den har minst en rad.
-- opt_out raderas aldrig och byggs aldrig om från en Excel-fil.
-- existing_customer och no_ftax speglar extern data och får uppdateras när den ändras.
CREATE TABLE suppression (
    orgnr      TEXT NOT NULL,
    reason     TEXT NOT NULL CHECK (reason IN ('opt_out', 'existing_customer', 'no_ftax')),
    created_at TEXT NOT NULL,
    note       TEXT,
    PRIMARY KEY (orgnr, reason)
);

CREATE TABLE call_list (
    id              INTEGER PRIMARY KEY,
    week            TEXT NOT NULL,       -- "2026w40"
    seller          TEXT NOT NULL,
    scoring_version TEXT NOT NULL,       -- vilka vikter som gav den här ordningen
    created_at      TEXT NOT NULL,
    file_path       TEXT,
    UNIQUE (week, seller)
);

-- Ögonblicksbilden av vad vi faktiskt skickade. Poäng och telefon är frusna
-- kopior med flit: signalerna får ändras efteråt, det vi mäter får inte.
CREATE TABLE call_list_row (
    id           INTEGER PRIMARY KEY,
    call_list_id INTEGER NOT NULL REFERENCES call_list(id) ON DELETE CASCADE,
    salon_id     INTEGER NOT NULL REFERENCES salon(id),
    rank         INTEGER NOT NULL,
    score        REAL NOT NULL,
    reasons      TEXT NOT NULL,          -- JSON-array med klartext
    phone        TEXT,
    UNIQUE (call_list_id, salon_id)
);

-- Inget CHECK på outcome: värdena är ett produktbeslut som kommer att ändras,
-- och contract.py är enda källan. Validering sker i Python vid import (D2).
CREATE TABLE outcome (
    call_list_row_id INTEGER PRIMARY KEY REFERENCES call_list_row(id) ON DELETE CASCADE,
    outcome          TEXT NOT NULL,
    comment          TEXT,
    imported_at      TEXT NOT NULL
);
