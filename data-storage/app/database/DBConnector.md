- [TO DISCUSS:](#to-discuss)
- [Basics](#basics)
  - [DBConnector](#dbconnector)
  - [loader](#loader)
- [Backlog](#backlog)
- [11.02.2025](#11022025)
- [10.02.2025 changelog](#10022025-changelog)
- [08.02.2025 changelog](#08022025-changelog)
- [07.02.2025 changelog](#07022025-changelog)

![Graph graph](loader/imgs/explained.png)
- тут +- зрозуміло які методи куди підійдуть

## TO DISCUSS:

- `sentiment_label` = to drop
- `sentiment_deviation_event` ? = to drop
- `sentiment_deviation_summary` ? = to drop
- `inconsistency_id` is a list of ids, no id as expected = must be single id, but it is possible to change to multiple ids i guess
- `inconsistencies` from scored = flag if there are at least 1 inconsistency detected for person, but we already have inconsistency_flag = can be ignored, it will be raplaced as sql query if needed
- `event_qt` - what to do with it correctly = same to incosistencies, can be replaced with proper sql query if needed

## Basics

### DBConnector

Бібліотека для роботи з нашою БД. Тут є пару конфігурацій (наприклад `DATABASE_URL`), які треба задавати, якщо будуть зміни. Тут задаються всі ORM таблиць, тому всі операції максимально захищені. Також всі операції логуються у файл із часом виконання де можливо.

> Зараз він якось працює, потрібен невеликий рефактор та тестування. Також можливо не всі функції присутні, проте є базові операції, за потреби все можна робити через `t_execute_query`. Я ДУЖЕ сподіваюсь що все врахував і помилок не буде

Для кожної таблиці так чи інакше існують такі методи (можуть бути відсутні коментарі):
- пошук по Non-null полям через `find_*`
- пошук з умовами (підтримує пошук типу `назва_поля=значення` та `назва_поля=(від, до)`), для `dimEvents` та `dimOpinions` присутній параметр processing_stage, у коментарях все описано
- додавання одиничного запису, з перевіркою на наявність через `add_*`
- додавання `Pandas` `DataFrame`, з перевіркою на наявність через `add_*_df`
- отримання деталей запису по його `id` через `get_*`
- отримання `Pandas` `DataFrame` по списку id
- зміна запису, в цілому зазначається `id` , а далі через необов'язкові параметри всі поля можна налаштувати через `update_*`
- видалення одиничного запису по id
- видалення по списку id

Також зробив кілька високорівневих функцій (вони всі починаються з `t_*`), які працюватимуть за один прогін:
- `t_execute_query()` - Виконує будь-який отриманий як `string` запит, працює попри ORM, тому він незахищений. Може повертати результат, якщо запит це включає.
- `t_get_events_input()` - по суті пошук, але одразу повертає `Pandas` `DataFrame`, processing_stage працює
- `t_get_opinions_input()` - те саме тільки для dimOpinion
- `t_get_article_chunk_content()` - по номеру чанка повертає фрагмент тексту, що відповідає відповідній статті та індексам
- `t_find_articles_without_chunks()` - шукає статті для якиї немає чанків, повертає список id
- `t_get_articles_without_chunks_input()` - те саме, але повертає одразу `Pandas` `DataFrame`


### loader

Ноутбук, з божою поміччю робочий, після прогону записує файли у БД. Поки налаштований на тимчасові рішення, але хоч як приклад послужить.

> Важливий нюанс - у всіх файлах з аналізом чомусь була тільки одна стаття як джерело. Я поки зробив це через заглушку `actual_article_id` , далі там треба буде використовувати `find_article()`


## Backlog

- ? to remake get_attitude (and similar) inputs to use tuples not dictionaries
- ? bugfix
- ? change logging levels


## 11.02.2025

- made the decision to separate updating methods: `update_events_df` for `dimEvents` (it's easier for current configuration, it still needs to be stitched to actual id, but in this case it can be done in less transactions) and `t_upload_person_event` for `dimOpinion` (it requeries complex stitching, so it cannot be done for all df at once in current configuration, only way is to use new method via `apply` for each row)
- FIXED: `t_upload_person_event` inconsistency stage bug - doesnt write in data properly, possibly bad bool-tinyint(1)-int converting
- FIXED: `t_upload_person_event` scoring step bug, needs to be traced (smths about `int(None)` conversion)
- FIXED: `inconsistency_flag` and all boolean bug (it must read and write bools correctly, DB uses tinyint(1) as BOOL for some reason)
- stripped date stamp in logs from milliseconds


## 10.02.2025 changelog

- chunk is_processed
- mass update via dataframe (only for `dimEvents` for now)
- import for `person_summary` (just using existing functions to write into `dimPerson` and `fctAttitude`, see `loader`)
- opinion anti-duplication bug fix
- bugfix for `DetachedInstanceError` (it worked fine, but somewhere it throwed an error) (unfortunately, still appears)
- top-level `t_upload_person_event`, still **WIP**

## 08.02.2025 changelog

- more complex table search
- get articles without chunks
- more top-level functions (inputs/outputs)
- proper transaction handling / ~~auto-refresh for funtions~~
- different-level logging
- Testing (hopefully) + boolean-int fix:
  > Added explicit bool conversion, so it should work. Copilot commentary:
  Using a Boolean type in SQLAlchemy, which maps to a TINYINT(1) in MySQL, should not cause issues with writing, getting, and comparing values. SQLAlchemy handles the conversion between Python's bool type and the database's TINYINT(1) type internally.


## 07.02.2025 changelog

- ORMs for all tables
- New function types (foe all tables (with some specifics for dimArticleChunks and fctAttitude)):
  > New operations:
  > - singe delete by id
  > - deleting list of ids
  > - adding record with all fields possible
  > - getting DataFrame from list of ids
  > - single deletion
  > - adjusted search (table search with conditions and `processing_stage` filter)
- Article chunks:
  > - _get articles without chunks_
  > - write chunk indexes
  > - view actual chunk from indexes
- Top - level functions (so-called)
  > Complex operations, usually utilizing regular functions
  > - update+insert mask
  > - raw sql execution