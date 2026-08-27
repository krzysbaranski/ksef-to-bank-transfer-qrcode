"""Budowanie treści kodu QR z danymi przelewu.

Obsługiwane standardy:

* ``zbp``  – Rekomendacja Związku Banków Polskich dot. kodu 2D dla płatności.
             Dziewięć pól rozdzielonych ``|``, kwota w groszach. To ten format
             skanują polskie aplikacje bankowe (mBank, PKO, Pekao, ING...).
* ``epc``  – EPC069-12 (SEPA Credit Transfer / "GiroCode"), używany przez banki
             strefy euro i m.in. Revolut.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

STANDARDY = ("zbp", "epc")

# Polskie znaki, dla których unicodedata nie daje sensownego rozkładu.
_ZAMIENNIKI = str.maketrans({"ł": "l", "Ł": "L", "ß": "ss", "æ": "ae", "ø": "o"})


class PaymentDataError(Exception):
    """Danych z faktury nie da się zamienić na poprawny przelew."""


@dataclass
class DanePrzelewu:
    odbiorca: str
    iban: str
    kwota: Decimal
    waluta: str = "PLN"
    tytul: str = ""
    nip: str | None = None
    kraj: str = "PL"
    bic: str | None = None
    nr_klienta: str | None = None


def bez_ogonkow(tekst: str) -> str:
    """Transliteracja do ASCII — część aplikacji bankowych gubi znaki diakrytyczne."""
    tekst = tekst.translate(_ZAMIENNIKI)
    rozlozony = unicodedata.normalize("NFKD", tekst)
    return "".join(znak for znak in rozlozony if not unicodedata.combining(znak))


def wyczysc(tekst: str, limit: int, ascii_only: bool = True) -> str:
    """Normalizuje białe znaki, usuwa ``|`` (separator ZBP) i przycina do limitu."""
    if ascii_only:
        tekst = bez_ogonkow(tekst)
    tekst = tekst.replace("|", "/")
    tekst = re.sub(r"\s+", " ", tekst).strip()
    return tekst[:limit].strip()


def znormalizuj_iban(numer: str) -> str:
    return re.sub(r"[\s-]", "", numer).upper()


def iban_poprawny(numer: str) -> bool:
    """Walidacja sumy kontrolnej ISO 13616 (mod 97 == 1)."""
    numer = znormalizuj_iban(numer)
    if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{13,32}", numer):
        return False
    przestawiony = numer[4:] + numer[:4]
    cyfry = "".join(
        str(ord(znak) - 55) if znak.isalpha() else znak for znak in przestawiony
    )
    return int(cyfry) % 97 == 1


def nrb_poprawny(nrb: str) -> bool:
    """Walidacja polskiego NRB (26 cyfr) — sprawdzana jako IBAN ``PL...``."""
    nrb = znormalizuj_iban(nrb)
    if not re.fullmatch(r"\d{26}", nrb):
        return False
    return iban_poprawny("PL" + nrb)


def _grosze(kwota: Decimal) -> int:
    return int((kwota * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def zbuduj_zbp(dane: DanePrzelewu, ascii_only: bool = True) -> str:
    """Ciąg zgodny z rekomendacją ZBP: 9 pól rozdzielonych ``|``."""
    if dane.waluta.upper() != "PLN":
        raise PaymentDataError(
            f"standard ZBP obsługuje wyłącznie PLN, faktura jest w {dane.waluta} "
            f"(użyj --standard epc)"
        )

    nrb = znormalizuj_iban(dane.iban)
    if nrb.startswith("PL"):
        nrb = nrb[2:]
    if not re.fullmatch(r"\d{26}", nrb):
        raise PaymentDataError(f"NRB musi mieć 26 cyfr, otrzymano: {dane.iban!r}")

    grosze = _grosze(dane.kwota)
    if grosze <= 0:
        raise PaymentDataError("kwota przelewu musi być większa od zera")
    # Rekomendacja przewiduje 6 znaków; dłuższe kwoty zapisujemy bez paddingu,
    # bo obcięcie zmieniłoby wartość przelewu.
    kwota = f"{grosze:06d}"

    nip = re.sub(r"\D", "", dane.nip or "")[:10]
    pola = [
        nip,
        dane.kraj.upper()[:2],
        nrb,
        kwota,
        wyczysc(dane.odbiorca, 20, ascii_only),
        wyczysc(dane.tytul, 32, ascii_only),
        wyczysc(dane.nr_klienta or "", 32, ascii_only),
        "",
        "",
    ]
    return "|".join(pola)


def zbuduj_epc(dane: DanePrzelewu, ascii_only: bool = False) -> str:
    """Ciąg zgodny z EPC069-12 (wersja 002, bez wymogu BIC)."""
    if dane.waluta.upper() != "EUR":
        raise PaymentDataError(
            f"standard EPC obsługuje wyłącznie EUR, faktura jest w {dane.waluta} "
            f"(użyj --standard zbp)"
        )

    iban = znormalizuj_iban(dane.iban)
    if not iban_poprawny(iban):
        raise PaymentDataError(f"nieprawidłowy IBAN: {dane.iban!r}")

    kwota = dane.kwota.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not (Decimal("0.01") <= kwota <= Decimal("999999999.99")):
        raise PaymentDataError(f"kwota poza zakresem EPC: {kwota}")

    linie = [
        "BCD",
        "002",
        "1",  # 1 = UTF-8
        "SCT",
        wyczysc(dane.bic or "", 11, ascii_only),
        wyczysc(dane.odbiorca, 70, ascii_only),
        iban,
        f"EUR{kwota}",
        "",  # kod celu płatności
        "",  # referencja strukturalna
        wyczysc(dane.tytul, 140, ascii_only),
        "",  # informacja dla odbiorcy
    ]
    return "\n".join(linie).rstrip("\n")


def zbuduj(standard: str, dane: DanePrzelewu, ascii_only: bool | None = None) -> str:
    if standard == "zbp":
        return zbuduj_zbp(dane, True if ascii_only is None else ascii_only)
    if standard == "epc":
        return zbuduj_epc(dane, False if ascii_only is None else ascii_only)
    raise PaymentDataError(
        f"nieznany standard {standard!r}, dostępne: {', '.join(STANDARDY)}"
    )
