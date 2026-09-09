# Append-only JSONL ledger, corrections as appended spans

Events are appended to one JSONL file per month and never rewritten. A correction is a new `span` record that overrides the sensors for its range, latest appended span winning on overlap. Sqlite was rejected because attribution is a stateful minute walk that SQL cannot express, so a database would add a schema and inserts only to hand back the same time-ordered list a file read already gives. Timewarrior's store was rejected because its interval model is manual start/stop.

Consequence: reports rescan the month files on every run and there is no derived store. That is a few MB per month, fine for years.
