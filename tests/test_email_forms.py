"""Email forms the normaliser must read, from the third review (blind corpus 2, findings F13 and F14).

CLASS-TABLE: every test in this file builds its rows by code over the characters a local part may hold and the ways an address is written around them, and each table also holds the input that must
still be refused (a space, a comma, two at signs). No database is needed.
"""
from __future__ import annotations

import itertools

from parcelkey.normalise import normalise_email

DOMAIN = "example.com"
# every character RFC 5322 lets into an unquoted local part besides letters, digits and the dot
ATEXT_SPECIALS = list("!#$%&'*+-/=?^_`{|}~")
# characters that are not allowed in an unquoted local part
NOT_ALLOWED = list(" ,;:()<>[]\"\\@")
FORMS = {          # how the address is written -> (text with {a} for the address, whether a display name is dropped)
    "plain": ("{a}", False),
    "padded": ("  {a}  ", False),
    "brackets": ("<{a}>", False),
    "name and brackets": ("Kate Smith <{a}>", True),
    "quoted name and brackets": ('"Smith, Kate" <{a}>', True),
    "name stuck to the bracket": ("K. Smith<{a}>", True),
    "name and brackets padded": ("  Kate <{a}> ", True),
    "mailto": ("mailto:{a}", False),
}


def assert_table(rows):
    bad = [r for r in rows if r[1] != r[2]]
    assert not bad, f"{len(bad)} of {len(rows)} rows differ, first: {bad[:5]}"


def test_class_email_local_part_characters_and_display_forms():
    """CLASS-TABLE: F13, F14. Every allowed local part character, in every written form, is kept, and a display name in front of angle brackets is dropped."""
    rows = []
    locals_ = [f"sean.o{c}brien" if c == "'" else f"first{c}last" for c in ATEXT_SPECIALS] + ["kate.smith", "kate+news", "first.last+promo", "a", "x1.y2", "o'brien", "d'arcy.o'neill"]
    for local, (form, (text, named)), upper in itertools.product(locals_, FORMS.items(), (False, True)):
        address = f"{local}@{DOMAIN}"
        written = text.format(a=address.upper() if upper else address)
        got, rules = normalise_email(written)
        rows.append((("address kept", local, form, upper), got, address))
        rows.append((("name rule", local, form, upper), "EMAIL_NAME_DROPPED" in rules, named))
        rows.append((("invalid never fires", local, form, upper), "EMAIL_INVALID" in rules, False))
        rows.append((("lower case rule", local, form, upper), "EMAIL_LOWERCASED" in rules, upper))
        rows.append((("normalising the result again changes nothing", local, form, upper), normalise_email(got), (got, ())))
    assert len(rows) > 1500
    assert_table(rows)


def test_class_email_characters_that_are_not_allowed_stay_refused():
    """CLASS-TABLE: F13. A space, a comma, a bracket, a quote or a second at sign in the local part is still an invalid email, in every written form."""
    rows = []
    for char, (form, (text, _)) in itertools.product(NOT_ALLOWED, FORMS.items()):
        got, rules = normalise_email(text.format(a=f"kate{char}smith@{DOMAIN}"))
        rows.append((("refused", char, form), (got, "EMAIL_INVALID" in rules), ("", True)))
    for bad in ["kate@", "@example.com", "kate@example", "kate@@example.com", "kate smith@example.com", "Kate Smith kate@example.com", "<>", "Kate <>", "Kate <a@b.com", "kate@exa mple.com",
                "kate@exam_ple.com", "Kate <a@example.com> <b@example.com>"]:
        got, rules = normalise_email(bad)
        rows.append((("refused text", bad), (got, "EMAIL_INVALID" in rules), ("", True)))
    assert_table(rows)


def test_a_blank_email_fires_nothing_and_the_address_is_not_edited():
    rows = [(("blank", raw), normalise_email(raw), ("", ())) for raw in (None, "", "   ")]
    rows.append((("dots kept", ), normalise_email("F.I.R.S.T@Gmail.com"), ("f.i.r.s.t@gmail.com", ("EMAIL_LOWERCASED",))))
    rows.append((("plus tag kept", ), normalise_email("kate+news@example.com"), ("kate+news@example.com", ())))
    rows.append((("apostrophe kept", ), normalise_email("sean.o'brien@example.com"), ("sean.o'brien@example.com", ())))
    rows.append((("a name in front changes nothing else", ), normalise_email("Kate Smith <Kate.Smith@Example.com>"), ("kate.smith@example.com", ("EMAIL_NAME_DROPPED", "EMAIL_LOWERCASED"))))
    assert_table(rows)


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# the fourth review (final blind recall of corpus-round-3, finding F6): an address with what a list or a sentence leaves behind it

TAIL_PUNCTUATION = ["", ";", ",", ".", ";;", ",;", " ;", " ,", ". ", "; ", ".."]
TAIL_NOTES = ["", " (Kate)", "(Kate)", " (work email)", "  (a, b)"]


def test_class_email_trailing_punctuation_and_a_bracket_note_are_dropped():
    """CLASS-TABLE: F6 (round 4). `kate@example.com;`, `kate@example.com.`, `kate@example.com (Kate)` and every mix of them, in every written form, give the clean address, so the same person's two
    spellings reach one contact."""
    rows = []
    locals_ = ["kate.smith", "kate+news", "o'brien", "a", "first-last", "x1.y2", f"first{ATEXT_SPECIALS[0]}last"]
    for local, (form, (text, named)), tail, note, upper in itertools.product(locals_, FORMS.items(), TAIL_PUNCTUATION, TAIL_NOTES, (False, True)):
        address = f"{local}@{DOMAIN}"
        written = text.format(a=address.upper() if upper else address)
        for ending in (tail + note, note + tail, tail + note + tail):
            got, rules = normalise_email(written + ending)
            label = (local, form, ending, upper)
            dropped = bool(ending.strip())
            rows.append((("address kept", *label), got, address))
            rows.append((("never invalid", *label), "EMAIL_INVALID" in rules, False))
            rows.append((("tail rule", *label), "EMAIL_TAIL_DROPPED" in rules, dropped))
            rows.append((("lower case rule", *label), "EMAIL_LOWERCASED" in rules, upper))
            rows.append((("same address as the clean one", *label), got, normalise_email(written)[0]))
            rows.append((("normalising the result again changes nothing", *label), normalise_email(got), (got, ())))
    assert len(rows) > 10000
    assert_table(rows)


def test_class_email_a_list_of_addresses_is_still_refused():
    """CLASS-TABLE: F6 (round 4). Two addresses in one cell, however they are separated and whatever follows them, are not one address: refused with EMAIL_INVALID, never read as the first."""
    rows = []
    for separator, tail, note in itertools.product(["; ", ";", ", ", ",", " ; ", " , ", " / ", " and ", " or ", " "], ["", ";", ".", ","], ["", " (both)"]):
        written = f"kate@{DOMAIN}{separator}jim@{DOMAIN}{tail}{note}"
        got, rules = normalise_email(written)
        rows.append((("list refused", written), (got, "EMAIL_INVALID" in rules), ("", True)))
    for bad in ["kate@example.com;jim", ";", ",", ".", "();", "(Kate)", "kate@example.com (Kate) jim@example.com", "kate@@example.com;"]:
        got, rules = normalise_email(bad)
        rows.append((("refused text", bad), (got, "EMAIL_INVALID" in rules), ("", True)))
    assert_table(rows)


def test_a_dot_inside_the_address_and_a_note_inside_a_display_name_are_not_trailing_text():
    rows = [
        (("dots kept", ), normalise_email("first.last@example.co.uk;"), ("first.last@example.co.uk", ("EMAIL_TAIL_DROPPED",))),
        (("note before the brackets", ), normalise_email("Kate (work) <kate@example.com>"), ("kate@example.com", ("EMAIL_NAME_DROPPED",))),
        (("note after the brackets", ), normalise_email("Kate <kate@example.com> (work);"), ("kate@example.com", ("EMAIL_TAIL_DROPPED", "EMAIL_NAME_DROPPED"))),
        (("a clean address fires nothing", ), normalise_email("kate@example.com"), ("kate@example.com", ())),
    ]
    assert_table(rows)
