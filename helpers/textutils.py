"""Text helpers: LaTeX stripping before prompting, tolerant evidence-quote matching."""
import html
import re
import unicodedata

_WRAP = re.compile(r"\\(?:mathrm|rm|text|textrm|textit|textbf|it|bf|mathbf|mathit|emph|mbox|hbox|mathcal|bm|boldsymbol|operatorname)\s*\{([^{}]*)\}")
_FONT = re.compile(r"\{\\(?:rm|it|bf|sl|sc|tt)\s+([^{}]*)\}")
_MATHML = re.compile(r"</?(?:math|mrow|msub|msup|msubsup|mi|mn|mo|mtext|mover|munder|mfrac|msqrt|mstyle|mspace|semantics|annotation)[^>]*>")
# any HTML/JATS tag incl. attributes (<p indent="0mm">, <sec>, <italic>); [^<>] keeps "500<m<800" intact
_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9:-]*(?:\s[^<>]*)?/?>")
_CMD = re.compile(r"\\([a-zA-Z]+)\s*")
_SYMBOLS = {"times": "×", "pm": "±", "sim": "~", "approx": "≈", "simeq": "≃", "lesssim": "≲",
            "gtrsim": "≳", "leq": "≤", "geq": "≥", "le": "≤", "ge": "≥", "to": "→",
            "rightarrow": "→", "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
            "Delta": "Δ", "eta": "η", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
            "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "chi": "χ",
            "omega": "ω", "Omega": "Ω", "ell": "ℓ", "infty": "∞", "sqrt": "√", "langle": "⟨",
            "rangle": "⟩", "bar": "", "overline": "", "hat": "", "tilde": "", "left": "",
            "right": "", "mathrm": "", "rm": "", "text": "", "it": "", "bf": ""}


def strip_latex(s: str) -> str:
    """Turn '$\\rm D^0$-tagged jets at $\\sqrt{s}=13$ TeV' into 'D^0-tagged jets at √s=13 TeV'."""
    if not s:
        return ""
    for _ in range(2):                                # HTML entities, sometimes double-escaped (&amp;nbsp;)
        s = html.unescape(s)
    s = s.replace("\xa0", " ")
    s = _MATHML.sub(" ", s) if "<math" in s else s   # MathML: keep the text nodes only
    s = _TAG.sub("", s)
    s = re.sub(r"\\surd\s*", "√", s)
    for _ in range(3):  # nested wrappers
        s = _WRAP.sub(r"\1", s)
        s = _FONT.sub(r"\1", s)
    s = _CMD.sub(lambda m: _SYMBOLS.get(m.group(1), m.group(1)), s)
    s = s.replace("$", "").replace("{", "").replace("}", "").replace("~", " ")
    s = s.replace("\\,", " ").replace("\\;", " ").replace("\\!", "").replace("\\%", "%")
    s = s.replace("\\", "")
    return re.sub(r"\s+", " ", s).strip()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", strip_latex(s)).lower()
    s = re.sub(r"[‐-―−]", "-", s)            # dash variants
    s = re.sub(r"[“”„\"'’‘`]", "", s)
    s = re.sub(r"[^\w\s%.,;:()+\-=/<>~≈±×→α-ω]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .;,")


def quote_match(quote: str, source: str) -> str:
    """Return 'exact' | 'normalized' | 'fragments' | 'not_found' | 'empty'.

    'fragments' = the quote was spliced with '...' but every piece (>= 4 words) is in the source.
    """
    if not quote or not quote.strip():
        return "empty"
    q = quote.strip().strip('"').strip()
    if q.rstrip(".") in source:
        return "exact"
    ns = _norm(source)
    if _norm(q) in ns:
        return "normalized"
    parts = [p for p in re.split(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*", q) if p.strip()]
    if len(parts) > 1 and all(len(p.split()) >= 4 and _norm(p) in ns for p in parts):
        return "fragments"
    return "not_found"
