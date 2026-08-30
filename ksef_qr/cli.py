"""Interfejs wiersza poleceń: faktura KSeF (XML) -> kod QR do przelewu."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import __version__, ksef, payment, render, whitelist


def _sciezki(wzorce: list[str]) -> list[Path]:
    """Rozwija katalogi do plików *.xml, zachowując kolejność i unikalność."""
    wynik: list[Path] = []
    for wzorzec in wzorce:
        sciezka = Path(wzorzec)
        if sciezka.is_dir():
            wynik.extend(sorted(sciezka.glob("*.xml")))
        else:
            wynik.append(sciezka)
    unikalne: list[Path] = []
    for sciezka in wynik:
        if sciezka not in unikalne:
            unikalne.append(sciezka)
    return unikalne


def _kolor_wlaczony(argumenty: argparse.Namespace) -> bool:
    if argumenty.no_color or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _tytul(faktura: ksef.Faktura, argumenty: argparse.Namespace) -> str:
    if argumenty.title:
        return argumenty.title
    if faktura.numer:
        return f"FV {faktura.numer}"
    return "Platnosc za fakture"


def _kwota(faktura: ksef.Faktura, argumenty: argparse.Namespace) -> Decimal:
    if argumenty.amount:
        try:
            return Decimal(argumenty.amount.replace(",", "."))
        except InvalidOperation:
            raise payment.PaymentDataError(
                f"nieprawidłowa kwota: {argumenty.amount!r}"
            ) from None
    kwota = faktura.do_zaplaty
    if kwota is None:
        raise payment.PaymentDataError(
            "faktura nie zawiera kwoty należności (P_15) — podaj --amount"
        )
    return kwota


def _rachunek(faktura: ksef.Faktura, argumenty: argparse.Namespace) -> ksef.Rachunek:
    if argumenty.account:
        return ksef.Rachunek(nrb=argumenty.account)
    if not faktura.rachunki:
        raise payment.PaymentDataError(
            "faktura nie zawiera numeru rachunku — podaj --account"
        )
    indeks = argumenty.account_index
    if not 0 <= indeks < len(faktura.rachunki):
        raise payment.PaymentDataError(
            f"--account-index {indeks} poza zakresem "
            f"(faktura ma {len(faktura.rachunki)} rachunk(i))"
        )
    return faktura.rachunki[indeks]


def _dane_przelewu(
    faktura: ksef.Faktura, argumenty: argparse.Namespace
) -> tuple[payment.DanePrzelewu, ksef.Rachunek]:
    rachunek = _rachunek(faktura, argumenty)
    nrb = rachunek.nrb_znormalizowany or ""
    odbiorca = argumenty.recipient or str(faktura.sprzedawca)
    return payment.DanePrzelewu(
        odbiorca=odbiorca,
        iban=nrb,
        kwota=_kwota(faktura, argumenty),
        waluta=(argumenty.currency or faktura.waluta).upper(),
        tytul=_tytul(faktura, argumenty),
        nip=faktura.sprzedawca.nip,
        bic=rachunek.swift,
        nr_klienta=(
            None if argumenty.no_client_number else faktura.nabywca.nr_klienta
        ),
    ), rachunek


def _sprawdz_biala_liste(
    faktura: ksef.Faktura, dane: payment.DanePrzelewu, argumenty: argparse.Namespace
) -> whitelist.WynikBialejListy | None:
    """Zwraca None, gdy użytkownik wyłączył sprawdzenie (--no-whitelist)."""
    if not argumenty.whitelist:
        return None
    return whitelist.sprawdz(
        dane.nip,
        dane.iban,
        data=argumenty.whitelist_date,
        timeout=argumenty.whitelist_timeout,
    )


def _podsumowanie(
    faktura: ksef.Faktura,
    dane: payment.DanePrzelewu,
    ostrzezenia: list[str],
    biala_lista: whitelist.WynikBialejListy | None = None,
) -> str:
    nrb = dane.iban
    nrb_czytelny = " ".join(
        [nrb[:2]] + [nrb[i : i + 4] for i in range(2, len(nrb), 4)]
    ) if len(nrb) == 26 else nrb

    wiersze = [
        ("Faktura", faktura.numer or "—"),
        ("Wystawiona", faktura.data_wystawienia or "—"),
        ("Sprzedawca", str(faktura.sprzedawca)),
        ("NIP sprzedawcy", faktura.sprzedawca.nip or "—"),
        ("Nabywca", str(faktura.nabywca)),
        ("Rachunek", nrb_czytelny or "—"),
        ("Kwota", f"{dane.kwota} {dane.waluta}"),
        ("Termin płatności", faktura.termin_platnosci or "—"),
        ("Tytuł przelewu", dane.tytul),
    ]
    if biala_lista is not None:
        wiersze.insert(
            6,
            ("Biała lista VAT", f"{biala_lista.znacznik} {biala_lista.komunikat}"),
        )
    if faktura.zaplata_czesciowa:
        wiersze.insert(
            7, ("Zapłacono częściowo", f"{faktura.zaplata_czesciowa} {faktura.waluta}")
        )

    szerokosc = max(len(etykieta) for etykieta, _ in wiersze)
    tekst = "\n".join(f"  {etykieta:<{szerokosc}}  {wartosc}" for etykieta, wartosc in wiersze)
    if ostrzezenia:
        tekst += "\n" + "\n".join(f"  ⚠ {ostrzezenie}" for ostrzezenie in ostrzezenia)
    return tekst


def _ostrzezenia(
    faktura: ksef.Faktura,
    dane: payment.DanePrzelewu,
    rachunek: ksef.Rachunek,
    biala_lista: whitelist.WynikBialejListy | None = None,
) -> list[str]:
    lista = []
    if biala_lista is not None and biala_lista.status == whitelist.NIEPOTWIERDZONY:
        lista.append(
            "BIAŁA LISTA: rachunku nie potwierdzono dla NIP sprzedawcy — "
            "nie wykonuj przelewu bez kontaktu z wystawcą"
        )
    if rachunek.pola_zamienione:
        lista.append(
            "wystawca zamienił pola rachunku miejscami — numer odczytany "
            "z NazwaBanku/OpisRachunku, zweryfikuj go przed przelewem"
        )
    if len(dane.iban) == 26 and not payment.nrb_poprawny(dane.iban):
        lista.append("numer rachunku ma niepoprawną sumę kontrolną")
    if faktura.zaplacono:
        lista.append(
            "faktura jest oznaczona jako zapłacona"
            + (f" ({faktura.data_zaplaty})" if faktura.data_zaplaty else "")
        )
    if faktura.forma_platnosci and faktura.forma_platnosci != "przelew":
        lista.append(f"forma płatności na fakturze: {faktura.forma_platnosci}")
    if len(faktura.rachunki) > 1:
        lista.append(
            f"faktura ma {len(faktura.rachunki)} rachunki — użyto pierwszego "
            f"(zmień przez --account-index)"
        )
    odbiorca_skrocony = payment.wyczysc(dane.odbiorca, 20)
    if payment.bez_ogonkow(dane.odbiorca).strip() != odbiorca_skrocony:
        lista.append(f"nazwa odbiorcy skrócona do 20 znaków: {odbiorca_skrocony!r}")
    return lista


def _slownik(
    faktura: ksef.Faktura,
    dane: payment.DanePrzelewu,
    tresc: str,
    biala_lista: whitelist.WynikBialejListy | None = None,
) -> dict:
    return {
        "faktura": {
            "numer": faktura.numer,
            "data_wystawienia": faktura.data_wystawienia,
            "data_sprzedazy": faktura.data_sprzedazy,
            "rodzaj": faktura.rodzaj,
            "wariant_schemy": faktura.wariant_schemy,
            "waluta": faktura.waluta,
            "netto": str(faktura.kwota_netto) if faktura.kwota_netto is not None else None,
            "vat": str(faktura.kwota_vat) if faktura.kwota_vat is not None else None,
            "brutto": str(faktura.kwota_brutto) if faktura.kwota_brutto is not None else None,
            "termin_platnosci": faktura.termin_platnosci,
            "forma_platnosci": faktura.forma_platnosci,
            "zaplacono": faktura.zaplacono,
            "dodatkowe_opisy": faktura.dodatkowe_opisy,
        },
        "sprzedawca": {
            "nip": faktura.sprzedawca.nip,
            "nazwa": faktura.sprzedawca.nazwa,
            "adres": faktura.sprzedawca.adres,
        },
        "nabywca": {
            "nip": faktura.nabywca.nip,
            "nazwa": faktura.nabywca.nazwa,
            "adres": faktura.nabywca.adres,
            "nr_klienta": faktura.nabywca.nr_klienta,
        },
        "rachunki": [
            {
                "nrb": rachunek.nrb_znormalizowany,
                "swift": rachunek.swift,
                "nazwa_banku": rachunek.nazwa_banku,
            }
            for rachunek in faktura.rachunki
        ],
        "przelew": {
            "odbiorca": dane.odbiorca,
            "nrb": dane.iban,
            "kwota": str(dane.kwota),
            "waluta": dane.waluta,
            "tytul": dane.tytul,
        },
        "biala_lista": (
            None
            if biala_lista is None
            else {
                "status": biala_lista.status,
                "komunikat": biala_lista.komunikat,
                "data": biala_lista.data,
                "request_id": biala_lista.request_id,
            }
        ),
        "qr": tresc,
    }


def _przetworz(sciezka: Path, argumenty: argparse.Namespace, wiele: bool) -> dict | None:
    faktura = ksef.parsuj_plik(str(sciezka))
    dane, rachunek = _dane_przelewu(faktura, argumenty)
    tresc = payment.zbuduj(argumenty.standard, dane, argumenty.ascii)
    biala_lista = _sprawdz_biala_liste(faktura, dane, argumenty)

    if (
        argumenty.strict_whitelist
        and biala_lista is not None
        and not biala_lista.potwierdzony
    ):
        raise payment.PaymentDataError(f"biała lista: {biala_lista.komunikat}")

    if argumenty.json:
        return _slownik(faktura, dane, tresc, biala_lista)

    if argumenty.payload:
        print(tresc)
        # stdout zostaje czysty do przekierowania, ostrzeżenia idą na stderr.
        for ostrzezenie in _ostrzezenia(faktura, dane, rachunek, biala_lista):
            print(f"{sciezka}: ⚠ {ostrzezenie}", file=sys.stderr)
        return None

    if wiele:
        print(f"\n\x1b[1m{sciezka.name}\x1b[0m" if _kolor_wlaczony(argumenty) else f"\n{sciezka.name}")

    print(
        _podsumowanie(
            faktura,
            dane,
            _ostrzezenia(faktura, dane, rachunek, biala_lista),
            biala_lista,
        )
    )
    print()

    qr = render.zbuduj_qr(tresc, border=argumenty.border)
    if not argumenty.no_qr:
        print(render.do_terminala(qr, kolor=_kolor_wlaczony(argumenty)))
        print()

    if argumenty.png:
        cel = _cel_pliku(argumenty.png, sciezka, ".png", wiele)
        render.do_png(qr, str(cel), box_size=argumenty.scale)
        print(f"  Zapisano: {cel}")
    if argumenty.svg:
        cel = _cel_pliku(argumenty.svg, sciezka, ".svg", wiele)
        render.do_svg(qr, str(cel))
        print(f"  Zapisano: {cel}")
    if argumenty.show_payload:
        print(f"  Treść QR: {tresc}")
    return None


def _cel_pliku(podana: str, zrodlo: Path, rozszerzenie: str, wiele: bool) -> Path:
    """Przy wielu fakturach traktuje --png jako katalog docelowy."""
    cel = Path(podana)
    if cel.is_dir() or (wiele and not cel.suffix):
        cel.mkdir(parents=True, exist_ok=True)
        return cel / (zrodlo.stem + rozszerzenie)
    if cel.parent != Path("."):
        cel.parent.mkdir(parents=True, exist_ok=True)
    return cel


def zbuduj_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ksef-qr",
        description=(
            "Czyta fakturę ustrukturyzowaną KSeF (XML, schemat FA) i generuje kod QR "
            "z danymi przelewu do zeskanowania w aplikacji bankowej."
        ),
        epilog=(
            "Przykłady:\n"
            "  ksef-qr faktura.xml\n"
            "  ksef-qr faktura.xml --png qr.png\n"
            "  ksef-qr ./faktury --png ./kody\n"
            "  ksef-qr faktura.xml --json | jq .przelew\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pliki", nargs="+", metavar="XML", help="pliki XML lub katalogi")
    parser.add_argument(
        "--version",
        action="version",
        version=(
            f"ksef-qr {__version__}\n"
            "Licencja GPL-2.0-only. Program nie jest objęty ŻADNĄ GWARANCJĄ."
        ),
    )

    grupa = parser.add_argument_group("dane przelewu")
    grupa.add_argument(
        "--standard",
        choices=payment.STANDARDY,
        default="zbp",
        help="zbp = polski kod 2D ZBP (domyślny), epc = SEPA EPC069-12",
    )
    grupa.add_argument("--amount", help="nadpisz kwotę przelewu")
    grupa.add_argument("--title", help="nadpisz tytuł przelewu")
    grupa.add_argument("--recipient", help="nadpisz nazwę odbiorcy")
    grupa.add_argument("--account", help="nadpisz numer rachunku (NRB/IBAN)")
    grupa.add_argument(
        "--account-index",
        type=int,
        default=0,
        metavar="N",
        help="który rachunek z faktury użyć (domyślnie 0)",
    )
    grupa.add_argument("--currency", help="nadpisz walutę")
    grupa.add_argument(
        "--no-client-number",
        action="store_true",
        help="nie umieszczaj numeru klienta w polu rezerwowym",
    )
    grupa.add_argument(
        "--ascii",
        dest="ascii",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="transliteruj polskie znaki (domyślnie tak dla zbp, nie dla epc)",
    )

    lista = parser.add_argument_group("biała lista podatników VAT")
    lista.add_argument(
        "--whitelist",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="sprawdź rachunek na białej liście MF wg NIP sprzedawcy "
        "(--no-whitelist pomija to sprawdzenie i pracuje bez sieci)",
    )
    lista.add_argument(
        "--whitelist-date",
        metavar="RRRR-MM-DD",
        help="dzień, na który sprawdzany jest rachunek (domyślnie dziś)",
    )
    lista.add_argument(
        "--whitelist-timeout",
        type=float,
        default=10.0,
        metavar="SEK",
        help="limit czasu zapytania do API (domyślnie 10)",
    )
    lista.add_argument(
        "--strict-whitelist",
        action="store_true",
        help="przerwij z błędem, jeśli rachunek nie został potwierdzony",
    )

    wyjscie = parser.add_argument_group("wyjście")
    wyjscie.add_argument("--png", metavar="ŚCIEŻKA", help="zapisz kod QR do pliku PNG")
    wyjscie.add_argument("--svg", metavar="ŚCIEŻKA", help="zapisz kod QR do pliku SVG")
    wyjscie.add_argument(
        "--scale", type=int, default=10, metavar="N", help="wielkość modułu PNG w px"
    )
    wyjscie.add_argument(
        "--border", type=int, default=2, metavar="N", help="szerokość marginesu QR"
    )
    wyjscie.add_argument("--json", action="store_true", help="wypisz dane jako JSON")
    wyjscie.add_argument(
        "--payload", action="store_true", help="wypisz samą treść kodu QR"
    )
    wyjscie.add_argument(
        "--show-payload", action="store_true", help="dopisz treść kodu pod QR"
    )
    wyjscie.add_argument("--no-qr", action="store_true", help="pomiń rysowanie QR")
    wyjscie.add_argument("--no-color", action="store_true", help="wyłącz kolory ANSI")
    return parser


def main(argv: list[str] | None = None) -> int:
    argumenty = zbuduj_parser().parse_args(argv)
    sciezki = _sciezki(argumenty.pliki)
    if not sciezki:
        print("Nie znaleziono plików XML.", file=sys.stderr)
        return 1

    wyniki = []
    bledy = 0
    for sciezka in sciezki:
        try:
            wynik = _przetworz(sciezka, argumenty, wiele=len(sciezki) > 1)
        except FileNotFoundError:
            print(f"{sciezka}: nie ma takiego pliku", file=sys.stderr)
            bledy += 1
        except (ksef.KsefParseError, payment.PaymentDataError) as blad:
            print(f"{sciezka}: {blad}", file=sys.stderr)
            bledy += 1
        else:
            if wynik is not None:
                wyniki.append(wynik)

    if argumenty.json and wyniki:
        print(json.dumps(wyniki if len(wyniki) > 1 else wyniki[0], ensure_ascii=False, indent=2))

    return 1 if bledy else 0


if __name__ == "__main__":
    sys.exit(main())
