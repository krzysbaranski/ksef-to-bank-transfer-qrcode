# ksef-qr

Aplikacja CLI, która czyta fakturę ustrukturyzowaną **KSeF** (XML, schemat FA — warianty 1, 2 i 3),
wyciąga z niej dane do przelewu i generuje **kod QR do zeskanowania w aplikacji bankowej**.

Kod QR rysowany jest wprost w terminalu (czarno-biały, niezależny od motywu — telefon go odczyta
z ekranu), a opcjonalnie zapisywany do PNG lub SVG.

## Instalacja

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Albo bez instalacji — wystarczy zależność `qrcode[pil]` i dołączony launcher:

```sh
python3 -m venv .venv && .venv/bin/pip install "qrcode[pil]"
./ksef-qr faktura.xml
```

## Użycie

```sh
ksef-qr faktura.xml                    # podsumowanie + kod QR w terminalu
ksef-qr faktura.xml --png qr.png       # dodatkowo zapis do PNG
ksef-qr ./faktury --png ./kody         # wsad: katalog XML -> katalog PNG
ksef-qr faktura.xml --payload          # sama treść kodu (do własnego generatora)
ksef-qr faktura.xml --json | jq .      # wszystkie odczytane dane jako JSON
ksef-qr faktura.xml --no-whitelist     # bez odpytywania API, całkowicie offline
```

Przykład:

```
  Faktura           1234/0126/ABC
  Wystawiona        2026-01-15
  Sprzedawca        Przykładowa Spółka Finansowa Sp. z o.o.
  NIP sprzedawcy    1111111111
  Nabywca           Jan Przykładowy
  Rachunek          34 9999 9999 1234 5678 9012 3456
  Biała lista VAT   ✓ rachunek przypisany do NIP sprzedawcy
  Kwota             553.5 PLN
  Termin płatności  2026-01-29
  Tytuł przelewu    FV 1234/0126/ABC
  ⚠ nazwa odbiorcy skrócona do 20 znaków: 'Przykladowa Spolka F'

  [kod QR]
```

(dane z `tests/resources/faktura_przykladowa.xml` — fikcyjnej faktury dołączonej do repozytorium)

### Nadpisywanie danych

Gdy faktura nie zawiera kompletu danych albo chcesz zapłacić inaczej niż wynika z dokumentu:

| Opcja | Działanie |
| --- | --- |
| `--amount 100.50` | inna kwota (np. rata, zaliczka) |
| `--title "Zaliczka"` | inny tytuł przelewu |
| `--recipient "Nazwa"` | inna nazwa odbiorcy |
| `--account NRB` | inny rachunek |
| `--account-index N` | wybór rachunku, gdy faktura ma ich kilka |
| `--currency EUR` | inna waluta |
| `--no-client-number` | nie umieszczaj numeru klienta w polu rezerwowym |

### Standardy kodu

| `--standard` | Opis |
| --- | --- |
| `zbp` (domyślny) | Rekomendacja ZBP „kod 2D” — format czytany przez polskie aplikacje bankowe. Tylko PLN. |
| `epc` | EPC069-12 / GiroCode (SEPA Credit Transfer). Tylko EUR. |

Format ZBP to dziewięć pól rozdzielonych `|`:

```
NIP|Kraj|NRB|Kwota w groszach|Nazwa odbiorcy|Tytuł|Rezerwa|Rezerwa|Rezerwa
1111111111|PL|34999999991234567890123456|055350|Przykladowa Spolka F|FV 1234/0126/ABC|12345678||
```

Limity narzucane przez standard (nazwa odbiorcy 20 znaków, tytuł 32) są egzekwowane, a skrócenie
nazwy sygnalizowane ostrzeżeniem. Polskie znaki są domyślnie transliterowane do ASCII
(`--no-ascii` wyłącza); w EPC zostają, bo standard używa UTF-8.

## Co jest odczytywane z faktury

Z sekcji `Fa` i `Podmiot1`/`Podmiot2`: numer faktury (`P_2`), daty (`P_1`, `P_6`), kwoty
(`P_13_1`, `P_14_1`, `P_15`), waluta, dane sprzedawcy i nabywcy, numer klienta, rachunki
(`Platnosc/RachunekBankowy` oraz `RachunekBankowyFaktora`), termin i forma płatności,
adnotacje o zapłacie oraz `DodatkowyOpis`.

Parser ignoruje namespace schemy, więc działa z FA(1), FA(2) i FA(3) bez zmian.

## Biała lista podatników VAT

Domyślnie przed wygenerowaniem kodu narzędzie pyta [API Ministerstwa Finansów](https://wl-api.mf.gov.pl/),
czy rachunek z faktury jest przypisany do NIP sprzedawcy:

```
GET https://wl-api.mf.gov.pl/api/check/nip/{NIP}/bank-account/{NRB}?date=RRRR-MM-DD
```

| Opcja | Działanie |
| --- | --- |
| `--no-whitelist` | pomija sprawdzenie — jedyny tryb działający bez sieci |
| `--whitelist-date RRRR-MM-DD` | sprawdza stan na wskazany dzień (domyślnie dziś) |
| `--whitelist-timeout SEK` | limit czasu zapytania (domyślnie 10 s) |
| `--strict-whitelist` | kończy z kodem 1, gdy rachunek nie został potwierdzony |

Wynik pojawia się w podsumowaniu jako `Biała lista VAT` i w JSON-ie pod kluczem `biala_lista`
(ze statusem, komunikatem, datą i `requestId` — MF traktuje ten identyfikator jako dowód
dochowania należytej staranności, więc warto go zachować przy większych przelewach).

Trzy rzeczy, o których trzeba wiedzieć:

- **`✗` nie znaczy „oszustwo".** API zwraca `NIE` także wtedy, gdy NIP nie istnieje albo
  sprzedawca nie jest czynnym podatnikiem VAT. Rachunki podmiotów zwolnionych z VAT i osób
  prywatnych nigdy nie będą potwierdzone.
- **Rachunki wirtualne** przypisane do rachunku masowego zwykle są rozpoznawane, ale nie zawsze —
  przy `✗` warto najpierw zadzwonić do wystawcy.
- **Sprawdzenie dotyczy tylko polskich NRB i wymaga sieci.** Dla zagranicznych IBAN-ów oraz
  bez połączenia status to `?` (niesprawdzony), a program działa dalej — brak API nigdy nie
  blokuje wygenerowania kodu.

Odpowiedzi są cache'owane w obrębie jednego uruchomienia, więc wsad kilkunastu faktur od tego
samego wystawcy to jedno zapytanie.

## Kontrole i ostrzeżenia

Przed wygenerowaniem kodu narzędzie sprawdza i sygnalizuje:

- rachunek niepotwierdzony na białej liście VAT (patrz sekcja wyżej),
- niepoprawną sumę kontrolną numeru rachunku (mod 97 wg ISO 13616),
- zamienione miejscami pola rachunku (spotykany błąd wystawców: numer rachunku
  w `NazwaBanku`, nazwa banku w `NrRB`) — numer jest odzyskiwany, ale sygnalizowany,
- fakturę oznaczoną jako już zapłaconą,
- formę płatności inną niż przelew (np. gotówka, karta),
- kilka rachunków na fakturze — użyto pierwszego,
- nazwę odbiorcy skróconą do limitu standardu.

Zapłaty częściowe (`ZaplataCzesciowa`) pomniejszają kwotę przelewu.

## Testy

```sh
.venv/bin/python -m pytest tests -q
```

Testy korzystają wyłącznie z fikcyjnej faktury `tests/resources/faktura_przykladowa.xml`
(wymyślone podmioty, NIP-y i rachunki z poprawnymi sumami kontrolnymi) — żadne prawdziwe
dane nie trafiają do repozytorium. Warstwa sieciowa białej listy jest w testach
podmieniana, więc suite nie odpytuje API MF.

## Struktura

```
ksef_qr/ksef.py      parser XML KSeF (stdlib, bez zależności)
ksef_qr/payment.py   budowa treści kodu (ZBP, EPC) + walidacja IBAN/NRB
ksef_qr/whitelist.py klient API białej listy podatników VAT
ksef_qr/render.py    rysowanie QR w terminalu, zapis PNG/SVG
ksef_qr/cli.py       argumenty, podsumowanie, obsługa wsadu
tests/resources/     fikcyjna faktura używana w testach
```

## Licencja i wyłączenie odpowiedzialności

Kod udostępniony na licencji [GNU General Public License v2.0](LICENSE) (GPL-2.0-only).
Wolno go używać, modyfikować i rozpowszechniać, ale jest to licencja typu **copyleft**:
rozpowszechniane wersje pochodne muszą być udostępnione na tej samej licencji, wraz z kodem
źródłowym.

Zgodnie z sekcjami 11 i 12 licencji oprogramowanie dostarczane jest **„TAKIM, JAKIE JEST"
(AS IS), bez jakiejkolwiek gwarancji** — wyraźnej ani dorozumianej, w tym gwarancji przydatności
handlowej i przydatności do określonego celu. Autor nie ponosi odpowiedzialności za żadne szkody
wynikłe z użycia programu — w szczególności za **przelewy wykonane na podstawie wygenerowanego
kodu QR**.

Ma to praktyczne znaczenie, bo narzędzie przetwarza dane finansowe:

- faktura może zawierać błędne albo celowo spreparowane dane (numer rachunku, kwotę),
- wystawcy zapisują dane niezgodnie ze schemą (patrz sekcja o zamienionych polach rachunku),
- limity standardu ZBP wymuszają skracanie nazwy odbiorcy i tytułu przelewu.

**Przed zatwierdzeniem płatności sprawdź w aplikacji bankowej, czy rachunek, kwota i odbiorca
zgadzają się z fakturą.** Zwróć uwagę na wypisywane ostrzeżenia (`⚠`) — sygnalizują dokładnie
te przypadki, w których dane z faktury budzą wątpliwości.
