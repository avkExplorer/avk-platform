-- Synthetic data for stg only (loaded by `avk seed __APP__`). Safe to run more than once.
insert into greeting (message)
select '__APP__ from the stg seed' where not exists (select 1 from greeting where message = '__APP__ from the stg seed');
