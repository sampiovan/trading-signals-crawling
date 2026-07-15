from crawler.comments import format_price_comment, format_loss_comment, parse_comment


# ----- format_price_comment -----

def test_format_non_jpy_four_decimals():
    assert format_price_comment('EURUSD', '1.33900') == '@1.3390'
    assert format_price_comment('GBPUSD', 1.34121) == '@1.3412'


def test_format_jpy_two_decimals():
    assert format_price_comment('USDJPY', '145.503') == '@145.50'
    assert format_price_comment('eurjpy', 158.1) == '@158.10'


def test_format_rounds_to_pip():
    assert format_price_comment('EURUSD', '1.125049') == '@1.1250'
    assert format_price_comment('EURUSD', '1.125051') == '@1.1251'


# ----- format_loss_comment -----

def test_loss_comment():
    assert format_loss_comment('1.3390', 120) == '@1.3390 (-120)'
    assert format_loss_comment('145.50', 1250) == '@145.50 (-1250)'


# ----- parse_comment -----

def test_parse_price_only():
    assert parse_comment('@1.3390') == ('1.3390', 0)


def test_parse_price_with_loss():
    assert parse_comment('@1.3390 (-120)') == ('1.3390', 120)
    assert parse_comment('@145.50 (-1250)') == ('145.50', 1250)


def test_parse_foreign_comments_return_none():
    # Posizioni manuali o di altri sistemi: non vanno toccate dalla guardia
    assert parse_comment('') is None
    assert parse_comment(None) is None
    assert parse_comment('placement') is None
    assert parse_comment('mio trade manuale') is None
    assert parse_comment('[sl 1.3390]') is None


def test_roundtrip_after_cut():
    # Il ciclo della guardia: parse -> cumulo -> nuovo commento -> parse
    price, loss = parse_comment('@1.3390 (-120)')
    new_comment = format_loss_comment(price, loss + 125)
    assert parse_comment(new_comment) == ('1.3390', 245)


def test_comment_fits_mt5_limit():
    # MT5 tronca i commenti oltre ~31 caratteri
    assert len(format_loss_comment('145.50', 99999)) <= 31
