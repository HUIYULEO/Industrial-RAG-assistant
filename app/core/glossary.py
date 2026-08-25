"""Small, curated terminology helper for the design-review MVP.

The glossary is deliberately code-backed for now: it is easy to review in a
pull request and requires no new persistence or administration UI.  It is a
language aid only; supplier documents remain the sole source of evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class GlossaryTerm:
    """One approved bilingual term and the spellings users may employ."""

    canonical: str
    chinese: str
    english: str
    abbreviations: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    definition: str = ""

    @property
    def labels(self) -> tuple[str, ...]:
        return (self.canonical, self.chinese, self.english, *self.abbreviations, *self.aliases)


# Start intentionally small. New terms should come from controlled URS/ES and
# supplier design specifications, then be reviewed by an engineer before being added.
GLOSSARY: tuple[GlossaryTerm, ...] = (
    GlossaryTerm(
        canonical="WCS",
        chinese="仓库控制系统",
        english="Warehouse Control System",
        abbreviations=("WCS",),
        aliases=("仓控系统", "warehouse control"),
        definition="The control layer coordinating material-handling equipment and execution flows.",
    ),
    GlossaryTerm(
        canonical="WES",
        chinese="仓库执行系统",
        english="Warehouse Execution System",
        abbreviations=("WES",),
        aliases=("仓储执行系统", "warehouse execution"),
        definition="The execution layer orchestrating warehouse work and order flow.",
    ),
    GlossaryTerm(
        canonical="WMS",
        chinese="仓库管理系统",
        english="Warehouse Management System",
        abbreviations=("WMS",),
        aliases=("仓储管理系统", "warehouse management"),
        definition="The management layer for inventory, orders, and warehouse operations.",
    ),
    GlossaryTerm(
        canonical="AGV",
        chinese="自动导引车",
        english="Automated Guided Vehicle",
        abbreviations=("AGV",),
        aliases=("无人搬运车",),
        definition="A vehicle that follows defined guidance for material transport.",
    ),
    GlossaryTerm(
        canonical="AMR",
        chinese="自主移动机器人",
        english="Autonomous Mobile Robot",
        abbreviations=("AMR",),
        aliases=("移动机器人",),
        definition="A mobile robot that navigates dynamically within its operating environment.",
    ),
    GlossaryTerm(
        canonical="MHE",
        chinese="物料搬运设备",
        english="Material Handling Equipment",
        abbreviations=("MHE",),
        aliases=("搬运设备",),
        definition="Equipment used to move, store, control, or protect materials in a warehouse.",
    ),
    GlossaryTerm(
        canonical="URS",
        chinese="用户需求规格说明书",
        english="User Requirements Specification",
        abbreviations=("URS",),
        aliases=("用户需求",),
        definition="The controlled document that states user and business requirements.",
    ),
    GlossaryTerm(
        canonical="FS",
        chinese="功能规格说明书",
        english="Functional Specification",
        abbreviations=("FS",),
        aliases=("功能规格",),
        definition="The supplier document describing intended functional behaviour.",
    ),
    GlossaryTerm(
        canonical="SDS",
        chinese="软件设计规格说明书",
        english="Software Design Specification",
        abbreviations=("SDS",),
        aliases=("软件设计",),
        definition="The supplier document describing software design, interfaces, configuration, and behaviour.",
    ),
    GlossaryTerm(
        canonical="HDS",
        chinese="硬件设计规格说明书",
        english="Hardware Design Specification",
        abbreviations=("HDS",),
        aliases=("硬件设计",),
        definition="The supplier document describing hardware design, interfaces, safety-related equipment, and installation constraints.",
    ),
    GlossaryTerm(
        canonical="FAT",
        chinese="工厂验收测试",
        english="Factory Acceptance Test",
        abbreviations=("FAT",),
        aliases=("工厂验收",),
        definition="Testing performed before delivery to demonstrate agreed behaviour.",
    ),
    GlossaryTerm(
        canonical="OPC UA",
        chinese="OPC 统一架构",
        english="OPC Unified Architecture",
        abbreviations=("OPC UA",),
        aliases=("OPC-UA",),
        definition="An industrial interoperability standard for secure data exchange.",
    ),
)


def find_relevant_terms(text: str, *, limit: int = 6) -> list[GlossaryTerm]:
    """Return approved terms explicitly mentioned in a question.

    ASCII abbreviations use word boundaries so, for example, ``FS`` does not
    match inside an unrelated English word. Chinese aliases are matched as
    literal phrases.
    """
    if not text or limit < 1:
        return []

    return [term for term in GLOSSARY if _matches(term, text)][:limit]


def terminology_context(text: str, *, limit: int = 6) -> str:
    """Format only the terms relevant to ``text`` for an LLM prompt."""
    terms = find_relevant_terms(text, limit=limit)
    if not terms:
        return ""

    lines = [
        "Relevant approved terminology (language guidance only; it is not supplier-document evidence):"
    ]
    for term in terms:
        names = f"{term.chinese} ({term.english}; {term.canonical})"
        lines.append(f"- {names}: {term.definition}")
    return "\n".join(lines)


def _matches(term: GlossaryTerm, text: str) -> bool:
    for label in term.labels:
        if not label:
            continue
        if label.isascii() and any(character.isalnum() for character in label):
            if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text, re.IGNORECASE):
                return True
        elif label.casefold() in text.casefold():
            return True
    return False
