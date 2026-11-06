# Compatibility-only MTA migrations in Agora

The `peer_assignment`, `peer_review`, `peer_evaluation`,
`peer_calibration`, and `peer_grade` packages in this branch contain only
their package marker and previously published Django migrations.

They are not installed applications, have no URLs, views, templates, jobs, or
runtime imports, and are not part of the Agora product. The migration files are
retained deliberately so the history of an existing shared-codebase database
remains inspectable and a rollback to the former deployed artifact does not
depend on files that disappeared from source control.

Deploying this branch does not drop or rewrite the corresponding historic
tables. Removing those tables or squashing this history requires a separate,
backed-up, rehearsed data-retirement project after every supported deployment
has crossed a documented checkpoint.

The shared `peer_course_course` and `peer_course_coursemember` tables also
contain legacy MTA columns that were historically created as non-nullable. A
state-only migration marks the corresponding model fields `editable=False`,
and Agora removes their MTA-only routes, mutation helpers, and other behavioral
consumers. They are therefore inert and are not presented by default admin or
model forms. The minimum ORM definitions remain because simply omitting a
physical `NOT NULL` column can make ordinary new-course or new-member inserts
fail on a deployed historical database.

Migration `peer_course.0074_mark_mta_schema_shims_inert` deliberately performs
no database operations and deletes no values. Dropping or making those columns
nullable is a later data-retirement operation, gated on a restored production
snapshot, inspection of actual constraints/defaults, rollback rehearsal, and
explicit approval. Until that gate is complete, the inert schema shims are a
compatibility boundary rather than active MTA product behavior.
