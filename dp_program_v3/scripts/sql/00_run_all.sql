/* DP Program V3 canonical SQL Server installer.

   Run this file from scripts/sql in SQLCMD mode:
     sqlcmd -b -S <server> -E -i .\00_run_all.sql

   The installer is idempotent and creates only the objects required by V3:
     - SEN and DWH schemas;
     - four DWH dimension/fact tables and one SEN backfill-state table;
     - fifteen SEN staging tables;
     - DWH.usp_LoadDirect contract v4;
     - the reviewed 37-symbol and 15-timeframe metadata.

   It never drops, truncates, or deletes existing data.
*/

:On Error exit

:r "01_setup_database.sql"
:r "02_core_tables.sql"
:r "03_staging_tables.sql"
:r "04_business_objects.sql"
:r "05_seed_symbols.sql"
:r "06_verify.sql"
