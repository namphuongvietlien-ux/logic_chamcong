"""Excel formula argument separator for Vietnamese / EU locales."""

ARG_SEP = ";"


def excel_formula(func: str, *args: str) -> str:
    """Build =FUNC(a;b;c) so Excel VN/EU does not treat comma as a decimal."""
    return f"={func}({ARG_SEP.join(str(a) for a in args)})"
