# Plan produkcyjny: integracja z nowym ERP

## Cel

Utrzymać aplikację na lokalnym, kontrolowanym modelu danych, zasilanym z nowego ERP:

- jednorazowy pelny bootstrap danych,
- codzienne delty (upsert),
- bezpieczne, odseparowane srodowisko importu.

## Architektura docelowa

1. **Source (ERP)**  
   Nowy ERP jako zrodlo danych.

2. **Ingestion zone (raw)**  
   Surowe exporty (JSON/CSV) w szyfrowanym storage.

3. **Transform / mapping**  
   Mapowanie do modelu aplikacji (`Machine`, `ServiceJob`, `Part`, `ServiceComment`, relacje).

4. **Load (upsert)**  
   Idempotentny zapis do FalkorDB (`MERGE`), bez kasowania calego grafu.

5. **Post-processing**  
   Przeliczenia po imporcie (np. ryzyko, co-occurrence, cache, sanity checks).

6. **App runtime**  
   Aplikacja dziala na lokalnych danych; brak zaleznosci live od ERP.

## Proces operacyjny

### 1) Bootstrap (one-time)

- Pobranie pelnego exportu z nowego ERP.
- Walidacja struktury i integralnosci (np. checksums).
- Import do warstwy raw.
- Transform + upsert do grafu.
- Zapis `snapshot_id` i `watermark` (max `editDate`).

### 2) Delta dzienna (cron)

- Preferowane: gotowy **delta export** z ERP po `editDate >= watermark`.
- Fallback: read-only API po timestampach.
- Job dzienny:
  - pobiera delte,
  - mapuje,
  - wykonuje upsert,
  - aktualizuje `watermark` dopiero po sukcesie.

### 3) Kontrola jakosci po imporcie

- Licznosci per encja (przed/po).
- Kontrola kluczy biznesowych (`erp_id`) i duplikatow.
- Raport zmian: `insert / update / unchanged / rejected`.
- Alert przy anomaliach (np. nagly spadek wolumenu).

## Bezpieczenstwo (must-have)

- Importer uruchamiany w prywatnym srodowisku (private subnet).
- Egress allowlist tylko do endpointow ERP (jesli API).
- Sekrety trzymane w Secret Manager/Vault (nie w repo).
- Szyfrowanie:
  - in transit (TLS),
  - at rest (storage + DB backups).
- Least privilege dla kont technicznych.
- Audyt: kto/kiedy/jaki wsad zaimportowal.
- Snapshot/backup przed importem + plan rollback.

## Limity API i throttling (jesli delta przez API)

Ograniczenia ERP:

- 500 darmowych wywolan dziennie per system (bez budzetu),
- 2 req/s per user, przekroczenie moze blokowac do 5 minut.

Wymagane mechanizmy:

- throttle: ~700-1000 ms miedzy requestami,
- brak rownoleglych wywolan jednym userem,
- limit calli na run (np. 450/dzien jako bufor),
- retry z backoff + cooldown po `too many requests`,
- idempotentny import (bez dubli przy retry).

## Szkic komponentow produkcyjnych

- `export_receiver` - odbiera plik exportu lub uruchamia read-only API pull,
- `delta_importer` - mapowanie + upsert,
- `reconcile_report` - raport jakosci i zgodnosci,
- `scheduler` - harmonogram (1x dziennie + manual rerun),
- `state_store` - `watermark`, statusy runow, metadane snapshotow.

## KPI operacyjne

- czas importu,
- liczba rekordow importowanych dziennie,
- procent odrzuconych rekordow,
- opoznienie danych (freshness),
- liczba retry/rate-limit incidents,
- pokrycie encji krytycznych dla UI.

## Decyzja implementacyjna

Model docelowy: **full export + daily delta + secure importer environment**.

