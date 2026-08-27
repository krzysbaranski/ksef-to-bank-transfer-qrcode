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
```
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
Limity narzucane przez standard (nazwa odbiorcy 20 znaków, tytuł 32) są egzekwowane, a skrócenie
nazwy sygnalizowane ostrzeżeniem. Polskie znaki są domyślnie transliterowane do ASCII
(`--no-ascii` wyłącza); w EPC zostają, bo standard używa UTF-8.

## Co jest odczytywane z faktury

Z sekcji `Fa` i `Podmiot1`/`Podmiot2`: numer faktury (`P_2`), daty (`P_1`, `P_6`), kwoty
(`P_13_1`, `P_14_1`, `P_15`), waluta, dane sprzedawcy i nabywcy, numer klienta, rachunki
(`Platnosc/RachunekBankowy` oraz `RachunekBankowyFaktora`), termin i forma płatności,
adnotacje o zapłacie oraz `DodatkowyOpis`.

Parser ignoruje namespace schemy, więc działa z FA(1), FA(2) i FA(3) bez zmian.

## Kontrole i ostrzeżenia

Przed wygenerowaniem kodu narzędzie sprawdza i sygnalizuje:

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

## Struktura

```
ksef_qr/ksef.py      parser XML KSeF (stdlib, bez zależności)
ksef_qr/payment.py   budowa treści kodu (ZBP, EPC) + walidacja IBAN/NRB
ksef_qr/render.py    rysowanie QR w terminalu, zapis PNG/SVG
ksef_qr/cli.py       argumenty, podsumowanie, obsługa wsadu
```
