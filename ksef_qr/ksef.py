"""Parser faktur ustrukturyzowanych KSeF (schemat FA, warianty 1-3).

Parsowanie jest celowo odporne na wersję schemy: nazwy elementów (P_15,
Platnosc, RachunekBankowy...) są stabilne między FA(1), FA(2) i FA(3),
zmienia się głównie namespace, dlatego jest on ignorowany.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

# Kody z pola Fa/Platnosc/FormaPlatnosci wg słownika KSeF.
FORMY_PLATNOSCI = {
    "1": "gotówka",
    "2": "karta",
    "3": "bon",
    "4": "czek",
    "5": "kredyt",
    "6": "przelew",
    "7": "mobilna",
}

# Kody z pola Fa/Platnosc/RachunekBankowy/RachunekWlasnyBanku.
RODZAJE_RACHUNKU = {
    "1": "rachunek banku",
    "2": "rachunek gospodarki własnej",
    "3": "rachunek cesyjny",
}


class KsefParseError(Exception):
    """Plik nie jest czytelną fakturą KSeF."""


@dataclass
class Podmiot:
    nip: str | None = None
    nazwa: str | None = None
    adres: str | None = None
    nr_klienta: str | None = None

    def __str__(self) -> str:
        return self.nazwa or self.nip or "?"


@dataclass
class Rachunek:
    nrb: str | None = None
    swift: str | None = None
    nazwa_banku: str | None = None
    opis: str | None = None
    rodzaj: str | None = None
    pola_zamienione: bool = False

    @property
    def nrb_znormalizowany(self) -> str | None:
        """NRB bez spacji i bez prefiksu kraju."""
        if not self.nrb:
            return None
        nrb = re.sub(r"[\s-]", "", self.nrb).upper()
        if nrb.startswith("PL"):
            nrb = nrb[2:]
        return nrb


@dataclass
class Faktura:
    numer: str | None = None
    data_wystawienia: str | None = None
    data_sprzedazy: str | None = None
    waluta: str = "PLN"
    kwota_netto: Decimal | None = None
    kwota_vat: Decimal | None = None
    kwota_brutto: Decimal | None = None
    rodzaj: str | None = None
    wariant_schemy: str | None = None
    sprzedawca: Podmiot = field(default_factory=Podmiot)
    nabywca: Podmiot = field(default_factory=Podmiot)
    rachunki: list[Rachunek] = field(default_factory=list)
    termin_platnosci: str | None = None
    forma_platnosci: str | None = None
    zaplacono: bool = False
    data_zaplaty: str | None = None
    zaplata_czesciowa: Decimal | None = None
    dodatkowe_opisy: dict[str, str] = field(default_factory=dict)

    @property
    def do_zaplaty(self) -> Decimal | None:
        """Kwota pozostała do zapłaty (brutto minus zapłaty częściowe)."""
        if self.kwota_brutto is None:
            return None
        if self.zaplacono:
            return Decimal("0")
        if self.zaplata_czesciowa:
            return max(self.kwota_brutto - self.zaplata_czesciowa, Decimal("0"))
        return self.kwota_brutto

    @property
    def rachunek(self) -> Rachunek | None:
        return self.rachunki[0] if self.rachunki else None


def _localname(tag: str) -> str:
    return tag.rpartition("}")[2]


def _find(el: ET.Element | None, *sciezka: str) -> ET.Element | None:
    """Schodzi po drzewie po nazwach lokalnych, ignorując namespace."""
    if el is None:
        return None
    biezacy: ET.Element | None = el
    for nazwa in sciezka:
        if biezacy is None:
            return None
        biezacy = next(
            (dziecko for dziecko in biezacy if _localname(dziecko.tag) == nazwa), None
        )
    return biezacy


def _findall(el: ET.Element | None, nazwa: str) -> list[ET.Element]:
    if el is None:
        return []
    return [dziecko for dziecko in el if _localname(dziecko.tag) == nazwa]


def _text(el: ET.Element | None, *sciezka: str) -> str | None:
    znaleziony = _find(el, *sciezka) if sciezka else el
    if znaleziony is None or znaleziony.text is None:
        return None
    wartosc = znaleziony.text.strip()
    return wartosc or None


def _kwota(el: ET.Element | None, *sciezka: str) -> Decimal | None:
    surowa = _text(el, *sciezka)
    if surowa is None:
        return None
    try:
        return Decimal(surowa.replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        return None


def _podmiot(el: ET.Element | None) -> Podmiot:
    dane = _find(el, "DaneIdentyfikacyjne")
    adres_el = _find(el, "Adres")
    linie = [
        _text(adres_el, "AdresL1"),
        _text(adres_el, "AdresL2"),
    ]
    return Podmiot(
        nip=_text(dane, "NIP"),
        # Osoby fizyczne nieprowadzące działalności mają ImiePierwsze/Nazwisko.
        nazwa=_text(dane, "Nazwa")
        or " ".join(
            czesc
            for czesc in (_text(dane, "ImiePierwsze"), _text(dane, "Nazwisko"))
            if czesc
        )
        or None,
        adres=", ".join(linia for linia in linie if linia) or None,
        nr_klienta=_text(el, "NrKlienta"),
    )


def _wyglada_jak_nrb(wartosc: str | None) -> bool:
    if not wartosc:
        return False
    oczyszczona = re.sub(r"[\s-]", "", wartosc).upper().removeprefix("PL")
    return bool(re.fullmatch(r"\d{26}", oczyszczona))


def _rachunki(platnosc: ET.Element | None) -> list[Rachunek]:
    wynik: list[Rachunek] = []
    # RachunekBankowyFaktora bywa jedynym rachunkiem przy fakturach z faktoringiem.
    for nazwa_wezla in ("RachunekBankowy", "RachunekBankowyFaktora"):
        for wezel in _findall(platnosc, nazwa_wezla):
            nrb = _text(wezel, "NrRB")
            nazwa_banku = _text(wezel, "NazwaBanku")
            opis = _text(wezel, "OpisRachunku")
            zamienione = False

            # Część systemów wystawców wpisuje numer rachunku do NazwaBanku
            # (lub OpisRachunku), a do NrRB nazwę banku. Ratujemy taki zapis,
            # zamiast odrzucać fakturę.
            if not _wyglada_jak_nrb(nrb):
                if _wyglada_jak_nrb(nazwa_banku):
                    nrb, nazwa_banku, zamienione = nazwa_banku, nrb, True
                elif _wyglada_jak_nrb(opis):
                    nrb, opis, zamienione = opis, nrb, True

            if not nrb:
                continue
            wynik.append(
                Rachunek(
                    nrb=nrb,
                    swift=_text(wezel, "SWIFT"),
                    nazwa_banku=nazwa_banku,
                    opis=opis,
                    rodzaj=RODZAJE_RACHUNKU.get(
                        _text(wezel, "RachunekWlasnyBanku") or ""
                    ),
                    pola_zamienione=zamienione,
                )
            )
    return wynik


def parsuj_plik(sciezka: str) -> Faktura:
    try:
        drzewo = ET.parse(sciezka)
    except ET.ParseError as blad:
        raise KsefParseError(f"nieprawidłowy XML: {blad}") from blad
    return parsuj_element(drzewo.getroot())


def parsuj_tekst(xml: str) -> Faktura:
    try:
        return parsuj_element(ET.fromstring(xml))
    except ET.ParseError as blad:
        raise KsefParseError(f"nieprawidłowy XML: {blad}") from blad


def parsuj_element(root: ET.Element) -> Faktura:
    if _localname(root.tag) != "Faktura":
        raise KsefParseError(
            f"element główny to <{_localname(root.tag)}>, oczekiwano <Faktura> (KSeF FA)"
        )

    fa = _find(root, "Fa")
    if fa is None:
        raise KsefParseError("brak sekcji <Fa> — to nie jest faktura KSeF")

    platnosc = _find(fa, "Platnosc")
    zaplacono_flaga = _text(platnosc, "Zaplacono")

    faktura = Faktura(
        numer=_text(fa, "P_2"),
        data_wystawienia=_text(fa, "P_1"),
        data_sprzedazy=_text(fa, "P_6"),
        waluta=_text(fa, "KodWaluty") or "PLN",
        kwota_netto=_kwota(fa, "P_13_1"),
        kwota_vat=_kwota(fa, "P_14_1"),
        kwota_brutto=_kwota(fa, "P_15"),
        rodzaj=_text(fa, "RodzajFaktury"),
        wariant_schemy=_text(root, "Naglowek", "WariantFormularza"),
        sprzedawca=_podmiot(_find(root, "Podmiot1")),
        nabywca=_podmiot(_find(root, "Podmiot2")),
        rachunki=_rachunki(platnosc),
        termin_platnosci=_text(platnosc, "TerminPlatnosci", "Termin"),
        forma_platnosci=FORMY_PLATNOSCI.get(_text(platnosc, "FormaPlatnosci") or ""),
        zaplacono=zaplacono_flaga == "1",
        data_zaplaty=_text(platnosc, "DataZaplaty"),
        zaplata_czesciowa=_kwota(platnosc, "ZaplataCzesciowa", "KwotaZaplatyCzesciowej"),
    )

    for opis in _findall(fa, "DodatkowyOpis"):
        klucz = _text(opis, "Klucz")
        wartosc = _text(opis, "Wartosc")
        if klucz and wartosc:
            faktura.dodatkowe_opisy.setdefault(klucz, wartosc)

    return faktura
