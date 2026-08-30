"""Weryfikacja rachunku na białej liście podatników VAT (API Ministerstwa Finansów).

Endpoint ``/api/check/nip/{nip}/bank-account/{nrb}?date=RRRR-MM-DD`` odpowiada
``{"result": {"accountAssigned": "TAK"|"NIE", ...}}``. Zwraca HTTP 200 również
wtedy, gdy NIP nie istnieje albo podmiot nie jest czynnym podatnikiem VAT —
w obu przypadkach odpowiedzią jest po prostu ``NIE``. Dlatego "NIE" oznacza
"nie potwierdzono", a nie dowód oszustwa.

Moduł korzysta wyłącznie z biblioteki standardowej i nigdy nie przerywa
generowania kodu QR: każdy problem z siecią kończy się statusem
``niesprawdzony``, nie wyjątkiem.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date as _date

API = "https://wl-api.mf.gov.pl"

POTWIERDZONY = "potwierdzony"
NIEPOTWIERDZONY = "niepotwierdzony"
NIESPRAWDZONY = "niesprawdzony"

# Odpowiedzi z API są niezmienne w obrębie doby, a jeden przebieg wsadowy
# potrafi pytać o ten sam rachunek wielokrotnie.
_CACHE: dict[tuple[str, str, str], "WynikBialejListy"] = {}


@dataclass
class WynikBialejListy:
    status: str
    komunikat: str
    request_id: str | None = None
    data: str | None = None

    @property
    def potwierdzony(self) -> bool:
        return self.status == POTWIERDZONY

    @property
    def znacznik(self) -> str:
        return {
            POTWIERDZONY: "✓",
            NIEPOTWIERDZONY: "✗",
        }.get(self.status, "?")


def wyczysc_cache() -> None:
    _CACHE.clear()


def _pobierz_json(url: str, timeout: float) -> dict:
    """Wydzielone, żeby testy mogły podmienić warstwę sieciową."""
    zadanie = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "ksef-qr"}
    )
    with urllib.request.urlopen(zadanie, timeout=timeout) as odpowiedz:
        return json.loads(odpowiedz.read().decode("utf-8"))


def _komunikat_bledu(blad: urllib.error.HTTPError) -> str:
    try:
        tresc = json.loads(blad.read().decode("utf-8"))
    except Exception:
        return f"HTTP {blad.code}"
    szczegoly = tresc.get("message") or tresc.get("result", {}).get("message")
    kod = tresc.get("code") or tresc.get("result", {}).get("code")
    if szczegoly and kod:
        return f"{szczegoly} ({kod})"
    return szczegoly or f"HTTP {blad.code}"


def sprawdz(
    nip: str | None,
    nrb: str | None,
    data: str | None = None,
    timeout: float = 10.0,
    api: str = API,
) -> WynikBialejListy:
    """Pyta białą listę, czy ``nrb`` jest przypisany do ``nip`` w dniu ``data``."""
    nip_cyfry = re.sub(r"\D", "", nip or "")
    nrb_cyfry = re.sub(r"[\s-]", "", (nrb or "")).upper().removeprefix("PL")
    dzien = data or _date.today().isoformat()

    if len(nip_cyfry) != 10:
        return WynikBialejListy(
            NIESPRAWDZONY, "brak poprawnego NIP sprzedawcy", data=dzien
        )
    if not re.fullmatch(r"\d{26}", nrb_cyfry):
        return WynikBialejListy(
            NIESPRAWDZONY, "biała lista obejmuje tylko polskie rachunki (NRB)", data=dzien
        )

    klucz = (nip_cyfry, nrb_cyfry, dzien)
    if klucz in _CACHE:
        return _CACHE[klucz]

    url = f"{api}/api/check/nip/{nip_cyfry}/bank-account/{nrb_cyfry}?date={dzien}"
    try:
        odpowiedz = _pobierz_json(url, timeout)
    except urllib.error.HTTPError as blad:
        wynik = WynikBialejListy(
            NIESPRAWDZONY, f"API białej listy odmówiło: {_komunikat_bledu(blad)}", data=dzien
        )
    except (urllib.error.URLError, TimeoutError, OSError) as blad:
        powod = getattr(blad, "reason", blad)
        wynik = WynikBialejListy(
            NIESPRAWDZONY, f"brak połączenia z API białej listy ({powod})", data=dzien
        )
    except (ValueError, json.JSONDecodeError):
        wynik = WynikBialejListy(
            NIESPRAWDZONY, "nieczytelna odpowiedź API białej listy", data=dzien
        )
    else:
        rezultat = (odpowiedz or {}).get("result") or {}
        przypisany = (rezultat.get("accountAssigned") or "").upper()
        request_id = rezultat.get("requestId")
        if przypisany == "TAK":
            wynik = WynikBialejListy(
                POTWIERDZONY,
                "rachunek przypisany do NIP sprzedawcy",
                request_id=request_id,
                data=dzien,
            )
        elif przypisany == "NIE":
            wynik = WynikBialejListy(
                NIEPOTWIERDZONY,
                "rachunku NIE ma na białej liście dla tego NIP "
                "(albo sprzedawca nie jest czynnym podatnikiem VAT)",
                request_id=request_id,
                data=dzien,
            )
        else:
            wynik = WynikBialejListy(
                NIESPRAWDZONY,
                "API białej listy nie zwróciło rozstrzygnięcia",
                request_id=request_id,
                data=dzien,
            )

    _CACHE[klucz] = wynik
    return wynik
