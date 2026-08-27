"""Rysowanie kodu QR w terminalu i zapis do plików."""

from __future__ import annotations

import qrcode

BIALY = "\x1b[48;2;255;255;255m"
CZARNY_TLO = "\x1b[48;2;0;0;0m"
CZARNY_ZNAK = "\x1b[38;2;0;0;0m"
BIALY_ZNAK = "\x1b[38;2;255;255;255m"
RESET = "\x1b[0m"


def zbuduj_qr(tresc: str, border: int = 2) -> qrcode.QRCode:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=border,
        box_size=10,
    )
    qr.add_data(tresc)
    qr.make(fit=True)
    return qr


def do_terminala(qr: qrcode.QRCode, kolor: bool = True) -> str:
    """Renderuje kod pół-blokami: dwa wiersze modułów na jeden wiersz tekstu.

    Kolory są wymuszone na czarno-białe (a nie zależne od motywu terminala),
    bo odwrócony kontrast bywa nieczytelny dla aparatu telefonu.
    """
    macierz = qr.get_matrix()
    szerokosc = len(macierz[0])
    pusty = [False] * szerokosc
    linie = []

    for indeks in range(0, len(macierz), 2):
        gorny = macierz[indeks]
        dolny = macierz[indeks + 1] if indeks + 1 < len(macierz) else pusty
        if not kolor:
            linie.append(
                "".join(
                    _znak_ascii(g, d) for g, d in zip(gorny, dolny, strict=True)
                )
            )
            continue

        bufor = []
        for gora, dol in zip(gorny, dolny, strict=True):
            # Moduł "dark" (True) rysujemy na czarno.
            bufor.append(
                (CZARNY_ZNAK if gora else BIALY_ZNAK)
                + (CZARNY_TLO if dol else BIALY)
                + "▀"
            )
        linie.append("".join(bufor) + RESET)

    return "\n".join(linie)


def _znak_ascii(gora: bool, dol: bool) -> str:
    if gora and dol:
        return "█"
    if gora:
        return "▀"
    if dol:
        return "▄"
    return " "


def do_png(qr: qrcode.QRCode, sciezka: str, box_size: int = 10) -> str:
    qr.box_size = box_size
    obraz = qr.make_image(fill_color="black", back_color="white")
    obraz.save(sciezka)
    return sciezka


def do_svg(qr: qrcode.QRCode, sciezka: str) -> str:
    import qrcode.image.svg

    obraz = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    obraz.save(sciezka)
    return sciezka
