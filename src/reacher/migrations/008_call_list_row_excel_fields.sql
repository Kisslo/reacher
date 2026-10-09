-- J-06: freeze all values shown in the generated Excel workbook.
ALTER TABLE call_list_row ADD COLUMN address TEXT NOT NULL DEFAULT '';
ALTER TABLE call_list_row ADD COLUMN town TEXT NOT NULL DEFAULT '';
ALTER TABLE call_list_row ADD COLUMN revenue TEXT NOT NULL DEFAULT '';
ALTER TABLE call_list_row ADD COLUMN result TEXT NOT NULL DEFAULT '';
