# Demo script (3 min): New ERP fresh data on existing machines

## Co juz jest przygotowane

- Subset export: `demo_subset_matched_3_more.json`
- Import wykonany do lokalnego grafu:
  - `machines_processed: 3`
  - `service_docs_processed: 5`
  - `comments_processed: 21`
  - `parts_rows_processed: 2`
- Dodatkowo masz juz poprzedni zestaw 3 maszyn (`demo_subset_matched_3.json`), czyli lacznie 6 gotowych case'ow.
- Dociagniety rozszerzony wsad historii dla tych 6 maszyn:
  - plik: `demo_subset_full_history_6.json`
  - import: `machines_processed: 6`, `service_docs_processed: 26`, `comments_processed: 101`, `parts_rows_processed: 33`

## Potwierdzenie "pelnego setu" dla demo maszyn

- Raport weryfikacyjny: `demo_full_set_verification_2026-04-22.json`
- Co oznacza "pelny set" w tym demie:
  - historia serwisowa (starsze i nowsze wpisy),
  - komentarze serwisowe,
  - zuzyte czesci (tam gdzie sa).
- Przykladowe pokrycie:
  - `1109`: historia `2025-01-21 -> 2026-03-19`, komentarze `32`, czesci `5`
  - `1144`: historia `2025-03-03 -> 2026-04-20`, komentarze `6`, czesci `2`
  - `157`: historia `2025-10-08 -> 2026-04-13`, komentarze `30`, czesci `3`
- Wazne doprecyzowanie: dla aktualnej shortlisty pola `source_new_erp` sa ustawione na rekordach historycznych i komentarzach, czyli te wpisy pochodza z nowego ERP (nie tylko same komentarze).

## Maszyny do pokazania (polecane)

1. `1109` - Falzanlage T700 401190  
   - latest new ERP edit: `2026-04-22 15:42:35`
2. `1144` - Schneidmaschine WPS92 MCS KV2 Nr 3068-030  
   - latest new ERP edit: `2026-04-22 11:09:58`
3. `157` - Falzmaschine M9 / R80 404406  
   - latest new ERP edit: `2026-04-22 12:48:16`

## Narracja (3 min)

### 0:00-0:30 - Teza

Powiedz:

> "Pokaze teraz, ze dla maszyn, ktore juz mamy w systemie, zasililismy swieze dane z nowego ERP (serwis i komentarze), bez pelnej migracji i bez live runtime dependency."

### 0:30-1:00 - Fleet (widok globalny)

1. Otworz `/einsatzplaner/fleet`.
2. Pokaz, ze to ten sam runtime system, ktory klient zna.
3. Przejdz do briefingu wybranej maszyny (klik w wiersz lub URL bezposredni ponizej).

### 1:00-2:30 - 2 szybkie case'y w Mission Briefing

Otwieraj bezposrednio:

- `/einsatzplaner/mission/1109`
- `/einsatzplaner/mission/1144`

Na kazdym case pokaz:

1. **Tozsamosc maszyny** (nazwa/serial) - to maszyna istniejaca juz w starym systemie.
2. **Sekcja History / Similar Cases** - swieze wpisy i komentarze.
3. **Dowod swiezosci** - odwolaj sie do latest edit timestamp z importu:
   - 1109: `2026-04-22 15:42:35`
   - 1144: `2026-04-22 11:09:58`

### 2:30-3:00 - Domkniecie (model produkcyjny)

Powiedz:

> "To dziala jako controlled ingestion: pobieramy dane z nowego ERP, robimy mapping i upsert do naszego modelu. Aplikacja dziala na lokalnym, zabezpieczonym store. Kolejny krok to codzienne delty po timestampach."

## Backup case (gdyby jeden ekran nie zaladowal sie od razu)

- `/einsatzplaner/mission/157` (M9 / R80, latest edit: `2026-04-22 12:48:16`)
- `/einsatzplaner/mission/73` (Wendestation TS, latest edit: `2026-04-22 16:40:43`)

## Jednozdaniowa odpowiedz na pytanie "czy to live?"

> "Live jest polaczenie i ekstrakcja z nowego ERP; runtime aplikacji jest celowo snapshot/delta-based ze wzgledow security i stabilnosci."

