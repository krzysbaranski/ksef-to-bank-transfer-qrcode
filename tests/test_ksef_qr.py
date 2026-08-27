from decimal import Decimal
from pathlib import Path

import pytest

from ksef_qr import ksef, payment
from ksef_qr.cli import main

PRZYKLAD = str(Path(__file__).resolve().parent / "resources" / "faktura_przykladowa.xml")


@pytest.fixture
def faktura() -> ksef.Faktura:
    return ksef.parsuj_plik(PRZYKLAD)


def test_parsowanie_naglowka(faktura):
    assert faktura.numer == "1234/0126/ABC"
    assert faktura.wariant_schemy == "3"
    assert faktura.waluta == "PLN"
    assert faktura.data_wystawienia == "2026-01-15"
    assert faktura.rodzaj == "VAT"


def test_parsowanie_kwot(faktura):
    assert faktura.kwota_netto == Decimal("450.0")
    assert faktura.kwota_vat == Decimal("103.5")
    assert faktura.kwota_brutto == Decimal("553.5")
    assert faktura.do_zaplaty == Decimal("553.5")


def test_parsowanie_podmiotow(faktura):
    assert faktura.sprzedawca.nip == "1111111111"
    assert faktura.sprzedawca.nazwa.startswith("Przykładowa Spółka")
    assert faktura.nabywca.nazwa == "Jan Przykładowy"
    assert faktura.nabywca.nr_klienta == "12345678"


def test_parsowanie_platnosci(faktura):
    assert faktura.termin_platnosci == "2026-01-29"
    assert faktura.forma_platnosci == "przelew"
    assert faktura.rachunek.nrb_znormalizowany == "34999999991234567890123456"
    assert faktura.rachunek.swift == "TESTPLPX"


def test_dodatkowe_opisy(faktura):
    assert faktura.dodatkowe_opisy["Numer VIN"] == "TESTVIN1234567890"


def test_nrb_z_faktury_ma_poprawna_sume_kontrolna(faktura):
    assert payment.nrb_poprawny(faktura.rachunek.nrb_znormalizowany)


def test_nrb_z_bledna_cyfra_odrzucony():
    assert not payment.nrb_poprawny("34999999991234567890123457")


def test_iban_poprawny():
    assert payment.iban_poprawny("PL34999999991234567890123456")
    assert payment.iban_poprawny("DE89370400440532013000")
    assert not payment.iban_poprawny("DE89370400440532013001")


def test_zbp_uklad_pol(faktura):
    dane = payment.DanePrzelewu(
        odbiorca=faktura.sprzedawca.nazwa,
        iban=faktura.rachunek.nrb_znormalizowany,
        kwota=faktura.do_zaplaty,
        tytul=f"FV {faktura.numer}",
        nip=faktura.sprzedawca.nip,
        nr_klienta=faktura.nabywca.nr_klienta,
    )
    pola = payment.zbuduj_zbp(dane).split("|")

    assert len(pola) == 9
    assert pola[0] == "1111111111"
    assert pola[1] == "PL"
    assert pola[2] == "34999999991234567890123456"
    assert pola[3] == "055350"  # 553,50 zł w groszach, 6 znaków
    assert pola[4] == "Przykladowa Spolka F"  # skrócone i przepisane na ASCII
    assert pola[5] == "FV 1234/0126/ABC"
    assert pola[6] == "12345678"
    assert pola[7:] == ["", ""]


def test_zbp_transliteruje_polskie_znaki():
    dane = payment.DanePrzelewu(
        odbiorca="Żółć Ćma Sp. z o.o.",
        iban="34999999991234567890123456",
        kwota=Decimal("1.00"),
        tytul="Opłata za łóżko",
    )
    pola = payment.zbuduj_zbp(dane).split("|")
    assert pola[4] == "Zolc Cma Sp. z o.o."
    assert pola[5] == "Oplata za lozko"


def test_zbp_zaokragla_grosze():
    dane = payment.DanePrzelewu(
        odbiorca="X", iban="34999999991234567890123456", kwota=Decimal("12.345")
    )
    assert payment.zbuduj_zbp(dane).split("|")[3] == "001235"


def test_zbp_kwota_powyzej_szesciu_cyfr_nie_jest_obcinana():
    dane = payment.DanePrzelewu(
        odbiorca="X", iban="34999999991234567890123456", kwota=Decimal("12345.67")
    )
    assert payment.zbuduj_zbp(dane).split("|")[3] == "1234567"


def test_zbp_odrzuca_obca_walute():
    dane = payment.DanePrzelewu(
        odbiorca="X", iban="34999999991234567890123456", kwota=Decimal("1"), waluta="EUR"
    )
    with pytest.raises(payment.PaymentDataError, match="wyłącznie PLN"):
        payment.zbuduj_zbp(dane)


def test_zbp_odrzuca_zerowa_kwote():
    dane = payment.DanePrzelewu(
        odbiorca="X", iban="34999999991234567890123456", kwota=Decimal("0")
    )
    with pytest.raises(payment.PaymentDataError, match="większa od zera"):
        payment.zbuduj_zbp(dane)


def test_zbp_usuwa_separator_z_tekstu():
    dane = payment.DanePrzelewu(
        odbiorca="A|B", iban="34999999991234567890123456", kwota=Decimal("1"), tytul="X|Y"
    )
    assert payment.zbuduj_zbp(dane).split("|") == [
        "", "PL", "34999999991234567890123456", "000100", "A/B", "X/Y", "", "", "",
    ]


def test_epc_uklad_linii():
    dane = payment.DanePrzelewu(
        odbiorca="Muster GmbH",
        iban="DE89370400440532013000",
        kwota=Decimal("12.3"),
        waluta="EUR",
        tytul="Rechnung 1",
        bic="COBADEFF",
    )
    linie = payment.zbuduj_epc(dane).split("\n")
    assert linie[:4] == ["BCD", "002", "1", "SCT"]
    assert linie[4] == "COBADEFF"
    assert linie[5] == "Muster GmbH"
    assert linie[6] == "DE89370400440532013000"
    assert linie[7] == "EUR12.30"
    assert linie[10] == "Rechnung 1"


def test_epc_odrzuca_zlodziejski_iban():
    dane = payment.DanePrzelewu(
        odbiorca="X", iban="DE89370400440532013001", kwota=Decimal("1"), waluta="EUR"
    )
    with pytest.raises(payment.PaymentDataError, match="IBAN"):
        payment.zbuduj_epc(dane)


def test_parser_odrzuca_obcy_xml(tmp_path):
    plik = tmp_path / "inne.xml"
    plik.write_text("<Cos><A/></Cos>")
    with pytest.raises(ksef.KsefParseError, match="oczekiwano <Faktura>"):
        ksef.parsuj_plik(str(plik))


def test_parser_dziala_bez_namespace():
    xml = """
    <Faktura>
      <Podmiot1><DaneIdentyfikacyjne><NIP>1111111111</NIP><Nazwa>A</Nazwa></DaneIdentyfikacyjne></Podmiot1>
      <Fa><P_2>1/2026</P_2><P_15>10.00</P_15>
        <Platnosc><RachunekBankowy><NrRB>34999999991234567890123456</NrRB></RachunekBankowy></Platnosc>
      </Fa>
    </Faktura>
    """
    faktura = ksef.parsuj_tekst(xml)
    assert faktura.numer == "1/2026"
    assert faktura.rachunek.nrb_znormalizowany == "34999999991234567890123456"


def test_faktura_zaplacona_ma_zero_do_zaplaty():
    xml = """
    <Faktura><Fa><P_15>100.00</P_15>
      <Platnosc><Zaplacono>1</Zaplacono><DataZaplaty>2026-01-01</DataZaplaty></Platnosc>
    </Fa></Faktura>
    """
    assert ksef.parsuj_tekst(xml).do_zaplaty == Decimal("0")


def test_zaplata_czesciowa_pomniejsza_kwote():
    xml = """
    <Faktura><Fa><P_15>100.00</P_15>
      <Platnosc><ZaplataCzesciowa><KwotaZaplatyCzesciowej>30.00</KwotaZaplatyCzesciowej></ZaplataCzesciowa></Platnosc>
    </Fa></Faktura>
    """
    assert ksef.parsuj_tekst(xml).do_zaplaty == Decimal("70.00")


def test_cli_payload(capsys):
    assert main([PRZYKLAD, "--payload"]) == 0
    wyjscie = capsys.readouterr().out.strip()
    assert wyjscie.startswith("1111111111|PL|34999999991234567890123456|055350|")


def test_cli_json(capsys):
    assert main([PRZYKLAD, "--json"]) == 0
    import json

    dane = json.loads(capsys.readouterr().out)
    assert dane["przelew"]["kwota"] == "553.5"
    assert dane["faktura"]["termin_platnosci"] == "2026-01-29"


def test_cli_nadpisanie_kwoty_i_tytulu(capsys):
    assert main([PRZYKLAD, "--payload", "--amount", "10", "--title", "Zaliczka"]) == 0
    pola = capsys.readouterr().out.strip().split("|")
    assert pola[3] == "001000"
    assert pola[5] == "Zaliczka"


def test_cli_png(tmp_path):
    cel = tmp_path / "qr.png"
    assert main([PRZYKLAD, "--no-qr", "--png", str(cel)]) == 0
    assert cel.stat().st_size > 0


def test_cli_blad_dla_nieistniejacego_pliku(capsys):
    assert main(["brak.xml"]) == 1
    assert "nie ma takiego pliku" in capsys.readouterr().err


def test_zamienione_pola_rachunku_sa_ratowane():
    # Spotykany w praktyce błąd wystawcy: NrRB zawiera nazwę banku,
    # a numer rachunku wylądował w NazwaBanku.
    xml = """
    <Faktura><Fa><P_2>1/2026</P_2><P_15>10.00</P_15>
      <Platnosc><RachunekBankowy>
        <NrRB>Bank Przykładowy S.A.</NrRB>
        <NazwaBanku>53 8888 8888 0000 0000 0000 0001</NazwaBanku>
      </RachunekBankowy></Platnosc>
    </Fa></Faktura>
    """
    rachunek = ksef.parsuj_tekst(xml).rachunek
    assert rachunek.nrb_znormalizowany == "53888888880000000000000001"
    assert rachunek.nazwa_banku == "Bank Przykładowy S.A."
    assert rachunek.pola_zamienione


def test_poprawny_rachunek_nie_jest_zamieniany(faktura):
    assert not faktura.rachunek.pola_zamienione
    assert faktura.rachunek.nazwa_banku.startswith("Bank Przykładowy")


def test_cli_ostrzega_o_zamienionych_polach(tmp_path, capsys):
    plik = tmp_path / "f.xml"
    plik.write_text(
        "<Faktura><Fa><P_2>1/2026</P_2><P_15>10.00</P_15><Platnosc><RachunekBankowy>"
        "<NrRB>Bank Przykładowy S.A.</NrRB><NazwaBanku>53888888880000000000000001</NazwaBanku>"
        "</RachunekBankowy></Platnosc></Fa></Faktura>"
    )
    assert main([str(plik), "--no-qr", "--no-color"]) == 0
    assert "zamienił pola rachunku" in capsys.readouterr().out
