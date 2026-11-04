"""The instructions each summary is written with. Changing one rewrites every summary of that
kind on the next run (the instructions are part of the input hash)."""

FORMAT = """\
Answer in exactly this form, plain text, no Markdown:
LINE: <one sentence, at most 25 words: what it is for>
SUMMARY: <3 to 6 sentences>"""

COMMON = """\
You write summaries for Steno, a map of an organization's software that AI agents use to find
which space, application, and flow a question is about. Your summary is what routing reads
first, so it must say what the thing is FOR in the organization's terms (the business purpose
and the domain words someone would use when asking about it), then how it does it.

Rules:
- Use only what the inputs say. Never invent features, users, or systems. If the purpose
  isn't clear from the inputs, say what it evidently does and say the purpose isn't stated.
- Prefer the domain's words (orders, invoices, data sources) over technical ones (endpoint,
  controller, repository).
- Name the main things it owns and the other applications or systems it depends on or
  serves, by their names in the inputs.
- Don't list every endpoint or table; group them by what they're for.
"""

APPLICATION = f"""{COMMON}
You are summarizing one APPLICATION from its README (when there is one), its ways in (each
flow's trigger and what its code says about itself), its ways out (tables, other
applications, external systems, topics), who uses it, and its entities.

{FORMAT}"""

SPACE = f"""{COMMON}
You are summarizing one SPACE: a group of applications. Say what the group is for as a whole
and what each major application contributes, so a question can be routed to the right space
and then the right application. Mention every application by name if there are ten or fewer.

{FORMAT}"""

ORGANIZATION = f"""{COMMON}
You are summarizing the ORGANIZATION from its spaces' summaries: what the organization's
software does as a whole, and what each space is responsible for.

{FORMAT}"""

SYSTEM = {"application": APPLICATION, "space": SPACE, "organization": ORGANIZATION}


def parse(text: str) -> tuple[str, str]:
    """(line, summary) from the answer. A reply that didn't follow the form keeps all its text
    as the summary, and its first sentence as the line."""
    line, summary = "", ""
    current = None
    for raw in text.strip().splitlines():
        if raw.startswith("LINE:"):
            current, line = "line", raw[5:].strip()
        elif raw.startswith("SUMMARY:"):
            current, summary = "summary", raw[8:].strip()
        elif current == "summary":
            summary = f"{summary} {raw.strip()}".strip()
        elif current == "line":
            line = f"{line} {raw.strip()}".strip()
    if not summary:
        summary = " ".join(text.split())
    if not line:
        line = summary.split(". ")[0].rstrip(".") + "."
    return line, summary
