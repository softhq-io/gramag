# Multimodal vs Text-Only Benchmark

_10 queries, same retrieval, two answer conditions._


**Totals:** text-only 740 words, multimodal 1174 words.
**Avg latency:** text-only 8.1s, multimodal 10.1s.


---

## spare-bearing — `smb`
**Q:** Welche Bestellnummer hat das Rillenkugellager in der Umlenkrolle x22781 und wie viele Stück pro Baugruppe?

**Hits:**
- `0.886` ManualSection SMB / 600A021408001_WP.pdf p.14
- `0.837` ManualSection SMB / 600A021408001_WP.pdf p.9
- `0.788` ManualSection SMB / 600A021408001_WP.pdf p.3
- `0.727` ManualSection SMB / 600A021408001_WP.pdf p.13
- `0.720` ManualSection SMB / 600A021408001_WP.pdf p.15
- `0.459` ImageAsset SMB / SMB.gif

### Text-only
_31 words, 0 citations, 3.7s_

Das Rillenkugellager in der Baugruppe **Umlenkrolle x22781** hat die Bestellnummer **4581** [CITE: SMB / 600A021408001_WP.pdf / page 13].

Pro Baugruppe sind **2 Stück** verbaut [CITE: SMB / 600A021408001_WP.pdf / page 13].

### Multimodal
_32 words, 0 citations, 5.4s_

Das Rillenkugellager in der Baugruppe **Umlenkrolle x22781_** hat die folgenden Spezifikationen:

*   **Bestellnummer (Best.-Nr.):** 4581
*   **Stückzahl:** 2 Stück pro Baugruppe
*   **Position:** Item 3

[CITE: SMB / 600A021408001_WP.pdf / page 13]

---

## power-supply-location — `smb`
**Q:** Wo sitzen die Schaltnetzteile T14 und T15 im Gehäuse und welche Klemmleiste ist darunter?

**Hits:**
- `0.812` ManualSection SMB / IS3027.PDF p.1
- `0.689` ManualSection SMB / IS3027.PDF p.2
- `0.687` ManualSection SMB / S03016.PDF p.7
- `0.686` ManualSection SMB / IS3027.PDF p.13
- `0.680` ManualSection SMB / 320A011408001_CE_deutsch.pdf p.1
- `0.432` ImageAsset SMB / SMB.gif

### Text-only
_70 words, 0 citations, 5.7s_

Die Schaltnetzteile **T14** und **T15** befinden sich im Schaltschrank auf der linken Seite der zweiten Montageschiene von oben [CITE: SMB / IS3027.PDF / page 2].

Direkt unter diesen beiden Netzteilen ist die Klemmleiste **X1** auf der darunterliegenden Schiene montiert [CITE: SMB / IS3027.PDF / page 2]. Gemäß dem Schaltplan handelt es sich bei T14 und T15 um 24VDC-Netzteile mit einem Ausgangsstrom von 2,5A [CITE: SMB / IS3027.PDF / page 13].

### Multimodal
_136 words, 0 citations, 7.4s_

Die Schaltnetzteile T14 und T15 sind im oberen Bereich des Schaltnetzteilgehäuses nebeneinander montiert, wobei **T14 auf der linken Seite** und **T15 auf der rechten Seite** sitzt [CITE: SMB / IS3027.PDF / page 13].

Direkt unterhalb dieser beiden Einheiten befindet sich die Klemmleiste **X21** [CITE: SMB / IS3027.PDF / page 13].

Zusätzliche Details zur Klemmleiste X21:
*   Sie dient dem Geräteanschluss der Schaltnetzteile und ist horizontal zentriert unter T14/T15 angeordnet [CITE: SMB / IS3027.PDF / page 13].
*   Die Klemmleiste verfügt über 8 Positionen, die paarweise gebrückt sind: **PE/PE**, **L1/L1**, **L2/L2** und **L3/L3** [CITE: SMB / IS3027.PDF / page 13].
*   Die Zuleitungen von T14 und T15 (Anschlüsse 1, 2, 3 und PE) führen von oben in die Klemmleiste, während die externen Anschlüsse (von Q1 und X20/PE-13) von unten kommen [CITE: SMB / IS3027.PDF / page 13].

---

## out-of-sequence — `adressiersystem-gui-netjet-1-cag-161-1204-007-00`
**Q:** Adressiersystem meldet 'Out of Sequence' — welche Registry-Parameter prüfen und welche Defaultwerte gelten?

**Hits:**
- `0.724` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN001B_-_Out_of_Sequence_&_Page_Differential_Errors_Exp_.pdf p.3
- `0.715` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN001B_-_Out_of_Sequence_&_Page_Differential_Errors_Exp_.pdf p.4
- `0.677` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN001B_-_Out_of_Sequence_&_Page_Differential_Errors_Exp_.pdf p.1
- `0.645` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN007B - Emulator Setup and Operation.pdf p.9
- `0.636` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN007B - Emulator Setup and Operation.pdf p.7
- `0.405` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / schwarz.bmp.bw.bmp

### Text-only
_36 words, 0 citations, 10.3s_

Bei einer „Out of Sequence“-Fehlermeldung meldet ein Imager, der weiter vom Sensor entfernt ist (downstream), den Abschluss eines Druckvorgangs an die GUI, bevor der näher am Sensor befindliche Imager (upstream) dies tut [CITE: Adressiersystem / IJAN001B_-_Out_of_Sequence_&

### Multimodal
_23 words, 0 citations, 13.8s_

Bei einem „Out of Sequence“-Fehler im Adressiersystem sind folgende Informationen relevant:

*   **Registry-Pfad:** Die Einstellungen befinden sich unter `HKEY_LOCAL_MACHINE\SOFTWARE\inc.jet\GUI\Settings\` [CITE: Adressiersystem / IJAN001B_-_Out_of_Sequence_&_Page_Differential_Errors_Exp_.

---

## gui-hardware — `adressiersystem-gui-netjet-1-cag-161-1204-007-00`
**Q:** Welche Hardware-Konfiguration wird für den NetJet 1 GUI empfohlen?

**Hits:**
- `0.613` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN001B_-_Out_of_Sequence_&_Page_Differential_Errors_Exp_.pdf p.1
- `0.437` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / schwarz.bmp.bw.bmp
- `0.408` ConfigFile Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / I_2019_09_02_07_08_42.txt
- `0.408` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN007B - Emulator Setup and Operation.pdf p.9
- `0.406` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN007B - Emulator Setup and Operation.pdf p.2
- `0.403` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN007B - Emulator Setup and Operation.pdf p.3

### Text-only
_37 words, 0 citations, 10.7s_

Die bereitgestellten Dokumente enthalten keine spezifischen Empfehlungen zur PC-Hardware-Konfiguration (wie Prozessor, Arbeitsspeicher oder Betriebssystem) für die NetJet 1 GUI.

Die Unterlagen zum **V4 Emulator** führen lediglich softwareseitige Einstellungen und Bedienungselemente auf:
*   **Imager-Unterstützung:** Der Emulator verfügt über

### Multimodal
_146 words, 0 citations, 12.3s_

Basierend auf den bereitgestellten Dokumenten sind keine spezifischen Empfehlungen für die Hardware-Konfiguration der NetJet 1 GUI (wie z. B. Anforderungen an Prozessor, Arbeitsspeicher oder Betriebssystem) enthalten.

Die vorliegenden Unterlagen beschreiben folgende Aspekte:
*   **Fehlerprüfung:** Die Dokumentation IJAN001B erläutert die Prüfung von "Out of Sequence"- und "Page Differential"-Fehlern [CITE: Adressiersystem GUI NetJet 1 / IJAN001B_-_Out_of_Sequence_&_Page_Differential_Errors_Exp_.pdf / page 1].
*   **System-Logs:** Ein Diagnose-Log zeigt eine Konfiguration mit drei Imagern (Imager 1, 2 und 3) und den dazugehörigen Tintenpatronen (Pens B, C und D), wobei Fehler bei der Patronen-ID und der Imager-Kommunikation aufgezeichnet wurden [CITE: Adressiersystem GUI NetJet 1 / config: I_2019_09_02_07_08_42.txt].
*   **Emulator-Software:** Das Dokument IJAN007B behandelt die Einrichtung und Bedienung eines V4-Emulators, einschließlich Menüoptionen wie "Save Bitmaps" und "Settings" (z. B. Repeat Length, Pixels Used per Page), enthält jedoch keine Hardware-Mindestanforderungen [CITE: Adressiersystem GUI NetJet 1 / IJAN007B - Emulator Setup and Operation.pdf / page 3, 9].

---

## abb-bitmap — `adressiersystem-gui-netjet-1-cag-161-1204-007-00`
**Q:** Co zawiera bitmapa ABB_600.bmp i dla jakiego typu przesyłek jest przeznaczona?

**Hits:**
- `0.347` ConfigFile Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / ENIWA619_MUSTER.TXT
- `0.319` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / schwarz.bmp
- `0.315` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / schwarz.bmp.bw.bmp
- `0.313` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / purge.bmp
- `0.302` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / HP_Deckblatt_0209_300.bmp
- `0.178` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / ABB_600.bmp

### Text-only
_89 words, 0 citations, 7.2s_

Bitmapa `ABB_600.bmp` zawiera następujące elementy:
*   Logotyp firmy **ABB** umieszczony w centralnej części.
*   Oznaczenie rodzaju przesyłki **A-Post** w lewym górnym rogu.
*   Napis **Boutique** w prawym górnym rogu.
*   Oznaczenie opłaty pocztowej **P.P. / Journal** w lewym dolnym rogu.
*   Miejscowość nadania **CH-5401 Baden** w prawym dolnym rogu.
*   Kod kwadratowy 2D (DataMatrix) znajdujący się po prawej stronie logotypu ABB.

Bitmapa jest przeznaczona dla przesyłek pocztowych typu **A-Post** oraz przesyłek typu **Journal** (gazety/czasopisma) nadawanych przez firmę ABB z siedzibą w Baden [CITE: Adressiersystem GUI NetJet 1 / ABB_600.bmp].

### Multimodal
_143 words, 0 citations, 6.8s_

Bitmapa `ABB_600.bmp` to cyfrowy szablon znaku opłaty pocztowej Poczty Szwajcarskiej (Die Post) [CITE: Adressiersystem / image: ABB_600.bmp].

**Zawartość bitmapy:**
*   **Kod DataMatrix:** Duży kod 2D umieszczony w lewym górnym rogu [CITE: Adressiersystem / image: ABB_600.bmp].
*   **Tekst:** Oznaczenia „GAS/ECR/ICR” oraz instrukcja „nicht frankieren” (nie frankować) wraz z jej odpowiednikami w języku francuskim („ne pas affranchir”) i włoskim („non affrancare”) [CITE: Adressiersystem / image: ABB_600.bmp].
*   **Numery identyfikacyjne:** 50778415 oraz 000435 [CITE: Adressiersystem / image: ABB_600.bmp].
*   **Symbole i logotypy:** 
    *   Duża litera „A” w lewym dolnym rogu (oznaczająca przesyłkę priorytetową A-Post).
    *   Logo „pro clima” (przedstawiające osobę i drzewo).
    *   Logo Poczty Szwajcarskiej („DIE POST” wraz z symbolem krzyża) [CITE: Adressiersystem / image: ABB_600.bmp].

**Przeznaczenie:**
Szablon jest przeznaczony dla **biznesowych przesyłek zwrotnych** (niem. *Geschäftsantwortsendungen*), o czym świadczy napis „nicht frankieren” (opłatę uiszcza odbiorca) oraz dla przesyłek typu **A-Post** [CITE: Adressiersystem / image: ABB_600.bmp].

---

## eniwa-job — `adressiersystem-gui-netjet-1-cag-161-1204-007-00`
**Q:** Jakie parametry ma zadanie ENIWA — jaki format, jakie liczniki, ile rekordów?

**Hits:**
- `0.621` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN005A_-_GUI_Hardware_Configuration_Guidelines.pdf p.2
- `0.616` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN001B_-_Out_of_Sequence_&_Page_Differential_Errors_Exp_.pdf p.3
- `0.614` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN005A_-_GUI_Hardware_Configuration_Guidelines.pdf p.5
- `0.613` ManualSection Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / IJAN005A_-_GUI_Hardware_Configuration_Guidelines.pdf p.3
- `0.419` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / HP_Deckblatt_0209_300.bmp
- `0.336` ConfigFile Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / ESTEE919A.TXT

### Text-only
_92 words, 0 citations, 6.0s_

Zadanie **ENIWA** (numer P47801) dla klienta Eniwa AG charakteryzuje się następującymi parametrami:

*   **Format:** 235 x 125 mm [CITE: Adressiersystem GUI NetJet 1 CAG-161.1204.007.00 / image: ENIWA_0209_300.bmp].
*   **Liczniki (Zähler):**
    *   Zähler 1: **1**
    *   Zähler 2: **1** [CITE: Adressiersystem GUI NetJet 1 CAG-161.1204.007.00 / image: ENIWA_0209_300.bmp].
*   **Liczba rekordów:** Zadanie obejmuje **11 300** rekordów (oznaczonych jako „Anzahl Datensätze” oraz „11300 Adr.”) [CITE: Adressiersystem GUI NetJet 1 CAG-161.1204.007.00 / image: ENIWA_0209_300.bmp].

Dodatkowe informacje o zadaniu:
*   **Zleceniodawca:** brain'print GmbH.
*   **Data/Godzina:** 02.09. 11:34 [CITE: Adressiersystem GUI NetJet 1 CAG-161.1204.007.00 / image: ENIWA_0209_300.bmp].

### Multimodal
_95 words, 0 citations, 8.3s_

Na podstawie dostarczonych materiałów dowodowych, informacje dotyczące konkretnego zadania o nazwie **ENIWA** są nieobecne.

Dostarczone dokumenty zawierają jedynie:
*   Wytyczne dotyczące konfiguracji sprzętowej GUI dla systemów inc.jet (parametry takie jak *Warming Pulse*, *Pen Driver Parameters*, *Pulse Length*) [CITE: Adressiersystem GUI NetJet 1 / IJAN005A_-_GUI_Hardware_Configuration_Guidelines.pdf / page 5].
*   Dane dla zadania klienta **Estee Lauder** (plik `ESTEE919A.TXT`), które obejmuje 6568 rekordów adresowych (*6568 Adr.*), licznik o nazwie **ZAEHLER** oraz 60-cyfrowy ciąg kontrolny **DAMA** [CITE: Adressiersystem GUI NetJet 1 / config: ESTEE919A.TXT].

W przekazanej dokumentacji nie ma wzmianki o formacie, licznikach ani liczbie rekordów dla zadania ENIWA.

---

## cmc-heizungen — `folieneinschlag-und-adressieranlage-cmc-2800-nr-4282`
**Q:** Wo sitzen die Heizungen an der CMC 2800 und welche Schemata gibt es dafür?

**Hits:**
- `0.695` ManualSection Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282 / Heizungen unten.pdf p.3
- `0.632` ManualSection Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282 / Heizungen unten.pdf p.2
- `0.625` ManualSection Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282 / Heizungen Schema.pdf p.1
- `0.601` ManualSection Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282 / Seriennummern.pdf p.1
- `0.419` ImageAsset Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282 / CSA.PCX
- `0.398` ConfigFile Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282 / IKEA  READ AND PRINT.JOB.txt

### Text-only
_191 words, 0 citations, 10.0s_

An der CMC 2800 (Nr. 4282) sind die Heizungen an mehreren Positionen verbaut, für die spezifische mechanische und elektrische Unterlagen vorliegen:

### 1. Positionen der Heizungen
*   **Heizungen unten:** Es gibt eine dedizierte Baugruppe für die unteren Heizungen [CITE: CMC 2800 / Nr 4282 / Heizungen unten.pdf / page 2].
*   **Schweiß- und Schneidevorrichtung:** Laut Schaltplan sind Heizwiderstände für den Schweißbalken (**Resistenza barra saldante**) und für den Schnitt (**Resistenza taglio**) vorhanden [CITE: CMC 2800 / Nr 4282 / Heizungen Schema.pdf / page 1].
*   **Schrumpftunnel (Forno):** Die Anlage umfasst einen Tunnel des Typs **FORNO R1** mit der Matrikelnummer 004314 [CITE: CMC 2800 / Nr 4282 / Seriennummern.pdf / page 1].

### 2. Verfügbare Schemata und technische Details
*   **Mechanisches Schema (Heizungen unten):**
    *   Die Zeichnung zeigt den Aufbau der unteren Heizeinheit.
    *   Zentrales Bauteil ist der **Heizwiderstand 230V 1000W** (Pos. 1) [CITE: CMC 2800 / Nr 4282 / Heizungen unten.pdf / page 2].
    *   Die Montage umfasst zudem Halterungen (Pos. 2), Isolierungen (Pos. 3) und spezifische Abdeckungen (Pos. 5) [CITE: CMC 2800 / Nr 4282 / Heizungen unten.pdf / page 3].
*   **Elektrisches Schema (Heizungen Schema):**
    *   Das Dokument "SCHEMA ELETTR

### Multimodal
_250 words, 0 citations, 10.8s_

An der CMC 2800 (speziell für die Anlage Nr. 4282 bzw. den Ofen „FORNO R1“) befinden sich die Heizungen im Bereich des Ofens, aufgeteilt in eine obere und eine untere Gruppe [CITE: CMC 2800 / Heizungen Schema.pdf / page 1] [CITE: CMC 2800 / Seriennummern.pdf / page 1]:

### Standorte und Komponenten
*   **Untere Heizgruppe (GRUPPO CAPPA INFERIORE):** Diese befindet sich in der unteren Haube des Ofens. Laut Stückliste (Tavola 4VT1417) sind hier **6 Widerstände** (Pos. 75, Art.-Nr. **1902470**, RESISTENZA AL. 220V 3000W L1550) verbaut [CITE: CMC 2800 / Heizungen unten.pdf / page 3].
*   **Obere Heizgruppe (GRUPPO RESISTENZE SUPERIORI):** Diese befindet sich im oberen Teil des Ofens [CITE: CMC 2800 / Heizungen Schema.pdf / page 1].

### Verfügbare Schemata
Es existiert ein spezifischer Elektroplan für die Ofenheizungen:
*   **Dokument:** „RESISTENZE FORNO / HEIZUNGEN OFEN“ (Zeichnungsnummer 0973502, Blatt 4) [CITE: CMC 2800 / Heizungen Schema.pdf / page 1].

**Details aus dem Schema:**
*   **Obere Gruppe (EH4.1 – EH4.8):** Besteht aus insgesamt 8 Heizelementen (6x 2000W und 2x 3000W), die über den Schaltschütz **KM5.1** (Typ D50B7) und den Hauptschalter **QU4.1** (40A) gesteuert werden [CITE: CMC 2800 / Heizungen Schema.pdf / page 1].
*   **Untere Gruppe (EH4.9 – EH4.14):** Besteht aus 6 Heizelementen (alle 3000W), gesteuert über den Schaltschütz **KM5.2** (Typ D50B7) und den Hauptschalter **QU4.2** (40A) [CITE: CMC 2800 / Heizungen Schema.pdf / page 1].
*   **Anschluss:** Die Heizungen sind für 230V ausgelegt und auf die drei Phasen (U1.1, V1.1, W1.1) verteilt [CITE: CMC 2800 / Heizungen Schema.pdf / page 1].

---

## smb-transport — `smb`
**Q:** Wie transportiere ich die SMB S03? Wo sind die Hebepunkte markiert?

**Hits:**
- `0.724` ManualSection SMB / d00678a.pdf p.6
- `0.689` ManualSection SMB / d00678a.pdf p.21
- `0.689` ManualSection SMB / S03016.PDF p.9
- `0.688` ManualSection SMB / d00709a.pdf p.6
- `0.655` ManualSection SMB / d01210a.pdf p.1
- `0.339` ImageAsset SMB / SMB.gif

### Text-only
_40 words, 0 citations, 10.7s_

Basierend auf den bereitgestellten Unterlagen können folgende Informationen zum Transport der SMB S03 gegeben werden:

*   **Maschinengewicht:** Beim Transport ist zu beachten, dass die SMB S03 ein Gewicht von ca. **300 kg** aufweist [CITE: SMB / d00678a.pdf / page 6

### Multimodal
_107 words, 0 citations, 13.8s_

Basierend auf den vorliegenden Unterlagen sind keine spezifischen Anweisungen zum Transportvorgang oder zur genauen Position der markierten Hebepunkte für die **SMB S03** enthalten.

Folgende relevante Informationen liegen jedoch vor:

*   **Maschinengewicht:** Die SMB S03 wiegt ca. **300 kg** [CITE: SMB / d00678a.pdf / Seite 6].
*   **Dokumentationshinweis:** Das Inhaltsverzeichnis der Montageanleitung (d01210a.pdf) führt das Kapitel „**Aufstellen**“ auf **Seite 3** auf [CITE: SMB / d01210a.pdf / Seite 1]. Diese spezifische Seite ist jedoch nicht Teil der bereitgestellten Beweismittel.

Die weiteren vorliegenden Seiten (Seite 21 von d00678a.pdf und Seite 9 von S03016.PDF) behandeln ausschließlich die Störungsbeseitigung bzw. elektrische Gerätelisten und enthalten keine Informationen zum physischen Transport oder zu Hebepunkten.

---

## cross-schemas — `cross-machine`
**Q:** Welche Maschinen haben Schaltpläne oder elektrische Schemata dokumentiert?

**Hits:**
- `0.709` ManualSection SMB / IS3027.PDF p.22
- `0.696` ManualSection SMB / 320A011408001_CE_deutsch.pdf p.1
- `0.689` ManualSection SMB / 600A021408001_EE_deutsch.pdf p.1
- `0.669` ManualSection SMB / 330A011408001_CE_deutsch.pdf p.1
- `0.421` ImageAsset Adressiersystem   GUI NetJet 1   CAG-161.1204.007.00 / purge.bmp
- `0.376` ConfigFile Folieneinschlag- und Adressieranlage   CMC 2800   Nr 4282 / BEORDA-J-ETIC.JOB.txt

### Text-only
_108 words, 0 citations, 6.2s_

Basierend auf den vorliegenden Dokumenten sind für folgende Maschinen Schaltpläne dokumentiert:

*   **SMB INO (Umreifungsmaschine):** Für die Seriennummer 320A011408001 ist der Schaltplan **IS3027** dokumentiert [CITE: SMB / 320A011408001_CE_deutsch.pdf / page 1]. Ergänzend liegt eine Geräteliste (IS3.027-99) vor, die elektrische Komponenten wie das Touchpanel (x43110), Ultraschallsensoren (B70: fr1889), induktive Näherungsschalter (z. B. B61: x37272) und diverse Sicherungen (z. B. F1/F2: cb6806) auflistet [CITE: SMB / IS3027.PDF / page 22].
*   **SMB S02 (Stautaktband):** Für die Seriennummer 600A021408001 ist der Schaltplan **S03016** dokumentiert [CITE: SMB / 600A021408001_EE_deutsch.pdf / page 1].
*   **SMB PRO (Umreifungsmaschine):** Für die Seriennummer 330A011408001 ist der Schaltplan **CS3037** dokumentiert [CITE: SMB / 330A011408001_CE_deutsch.pdf / page 1].

### Multimodal
_115 words, 0 citations, 8.7s_

Basierend auf den vorliegenden Dokumenten sind für folgende Maschinen spezifische Schaltpläne dokumentiert:

*   **SMB INO (Umreifungsmaschine):** Für die Maschine mit der Seriennummer 320A011408001 ist der Schaltplan **IS3027** dokumentiert [CITE: SMB / 320A011408001_CE_deutsch.pdf / page 1]. Ergänzend dazu existiert eine Geräteliste (Seite 1/3) für diesen Schaltplan (IS3.027-99), die unter anderem Sensoren (z. B. Ultraschallsensor UB1000, Pos. B70, Best.-Nr. fr1889) und Sicherungen auflistet [CITE: SMB / IS3027.PDF / page 22].
*   **SMB S02 (Stautaktband):** Für die unvollständige Maschine mit der Seriennummer 600A021408001 ist der Schaltplan **S03016** hinterlegt [CITE: SMB / 600A021408001_EE_deutsch.pdf / page 1].
*   **SMB PRO (Umreifungsmaschine):** Für die Maschine mit der Seriennummer 330A011408001 ist der Schaltplan **CS3037** dokumentiert [CITE: SMB / 330A011408001_CE_deutsch.pdf / page 1].

---

## smb-24m-maintenance — `smb`
**Q:** Welche Baugruppen gehören zur 2.4 Millionen Wartungsstufe und welche Teile werden getauscht?

**Hits:**
- `0.748` ManualSection SMB / 600A021408001_WP.pdf p.9
- `0.741` ManualSection SMB / 600A021408001_WP.pdf p.3
- `0.733` ManualSection SMB / d00678a.pdf p.21
- `0.665` ManualSection SMB / 600A021408001_WP.pdf p.5
- `0.662` ManualSection SMB / d00678a.pdf p.5
- `0.414` ImageAsset SMB / SMB.gif

### Text-only
_46 words, 0 citations, 10.3s_

 / page 9]. Correct.

    *   *One more thing:* The question asks "welche Teile werden getauscht". In the context of a maintenance manual index, the items listed under a cycle count are the items to be replaced/serviced.

    *   *Final Answer Construction:*
        Für das **Stautaktband SMB S.02 (60

### Multimodal
_127 words, 0 citations, 13.7s_

Für das **Stautaktband SMB S.02 - 2 Staustationen (Modell-Nr. 600A021408001)** sind in der Wartungsstufe **2.4 Millionen Umreifungen** (2.4 million cycles) laut Inhaltsverzeichnis folgende Baugruppen und Teile für den Austausch bzw. die Wartung vorgesehen:

*   **Umlenkrolle kpl.** (Idler roll compl.) [CITE: SMB / 600A021408001_WP.pdf / page 9]
*   **Umlenkrolle** (Deflection roll compl.) [CITE: SMB / 600A021408001_WP.pdf / page 9]
*   **Spannrolle kpl. schmale Riemenspur** (Belt pulley) [CITE: SMB / 600A021408001_WP.pdf / page 9]

**Wichtige Hinweise für den Techniker:**
*   Es dürfen ausschließlich **Original-Ersatzteile** des Herstellers verwendet werden, da andernfalls die Produkthaftung erlischt [CITE: SMB / 600A021408001_WP.pdf / page 5].
*   Vor Beginn der Arbeiten muss die Maschine vom Stromnetz getrennt oder der **Hauptschalter auf „OFF“** gestellt und mit einem Schloss gegen Wiedereinschalten gesichert werden [CITE: SMB / d00