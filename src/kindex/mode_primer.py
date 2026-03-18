"""
Mode primer extraction and generation for kindex.

Generates conversation-priming artifacts from session fingerprints.
Based on activation geometry research (Papers 35-40): induced understanding
outperforms direct instruction, 15 tokens capture 98.8% of priming benefit,
and adaptive guidance is actively harmful.

The primer is NOT instructions. It is a state induction — a short artifact
that shifts the AI's processing mode rather than specifying its behavior.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

# The extraction prompt is the most important artifact in this module.
# It must produce a primer that:
# 1. Sets processing MODE, not task specification
# 2. Is short (~50 words for the primer itself)
# 3. Establishes RELATIONSHIP, not role
# 4. Explicitly permits domain shifts and tangents
# 5. Constrains OUTPUT quality, not PROCESS
#
# Research basis:
# - Paper 36: 15 tokens = 98.8% of priming benefit
# - Paper 37: Adaptive guidance is actively harmful
# - Paper 38: Every mechanism that adds context is interference
# - Paper 39: Reset + prime beats expert baseline by 39%
# - Paper 35: Induced understanding outperforms instruction 5.4x

EXTRACTION_PROMPT = """You are extracting a conversation mode primer from a session fingerprint.

A primer is NOT instructions. It is a state induction — a short passage that shifts an AI's
processing mode the way a key signature shifts a musician's tonal center. The AI that reads it
should immediately know HOW to think, not WHAT to think about.

Research shows:
- 15 tokens of mode-setting captures 98.8% of achievable priming benefit
- Adaptive guidance (detailed instructions, checklists, style guides) is ACTIVELY HARMFUL
- Every mechanism that adds to the AI's context either contributes nothing or contributes interference
- Induced understanding (shared processing mode) outperforms direct instruction by 5.4x
- Cross-domain shifts IMPROVE quality — domain discontinuity acts as natural mode reset

The primer must:
1. Establish a RELATIONSHIP between human and AI (collaborator, architect, sparring partner)
   — not a role for the AI to play
2. Set the REGISTER (precision level, formality, challenge tolerance)
   — not behavioral rules
3. State what QUALITY means for this mode (correctness? novelty? honesty about uncertainty?)
   — not process constraints
4. Explicitly PERMIT domain shifts, tangents, incomplete thoughts, and pushback
   — because restricting these degrades conversation quality
5. Be SHORT — under 80 words for the primer body. Every additional word is interference.

You will receive:
- A mode name
- A fingerprint: concepts, decisions, questions, constraints, and watches from sessions in this mode
- Optionally: the human's communication style observations

Generate THREE sections:

## Primer
The mode-setting passage. Under 80 words. No bullet points. No instructions. A statement
of what this space is for and how we operate in it.

## Boundary
2-3 sentences defining what QUALITY means in this mode. What must be true about the output.
Not how to produce it.

## Permissions
One sentence explicitly granting freedom. Domain shifts, pushback, tangents, "I don't know,"
changing direction mid-thought — whatever this mode needs to stay alive rather than calcify.

Do NOT include:
- Behavioral instructions ("always," "never," "make sure to")
- Process specifications ("first do X, then Y")
- Style guides ("use bullet points," "be concise")
- Role-play framing ("you are a senior engineer")
These are shepherd content. They degrade output quality.
"""


@dataclass
class ModeFingerprint:
    """The structural residue of a conversation mode."""
    concepts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    watches: list[str] = field(default_factory=list)
    style_observations: Optional[str] = None


@dataclass
class ModePrimer:
    """A conversation-priming artifact."""
    name: str
    primer: str
    boundary: str
    permissions: str
    fingerprint: ModeFingerprint
    version: int = 1

    def to_injection(self, session_context: Optional[str] = None) -> str:
        """Generate the artifact to inject into a new AI session.

        Structure: reset boundary, then primer, then session context if resuming.
        Research basis (Paper 39): reset before prime beats expert baseline by 39%.
        """
        parts = []

        # Reset boundary
        parts.append("---")

        # Primer
        parts.append(self.primer)

        # Boundary (what quality means)
        parts.append(self.boundary)

        # Permissions
        parts.append(self.permissions)

        # Session context (fingerprint from prior session, if resuming)
        if session_context:
            parts.append("---")
            parts.append("Prior context (structural residue, not transcript):")
            parts.append(session_context)

        return "\n\n".join(parts)

    def to_export(self) -> dict:
        """PII-scrubbed portable format."""
        return {
            "name": self.name,
            "primer": self.primer,
            "boundary": self.boundary,
            "permissions": self.permissions,
            "version": self.version,
            # Fingerprint excluded from export by default — it may contain specifics
            # User can opt-in with --include-fingerprint
        }

    def to_json(self) -> str:
        return json.dumps({
            "name": self.name,
            "primer": self.primer,
            "boundary": self.boundary,
            "permissions": self.permissions,
            "fingerprint": {
                "concepts": self.fingerprint.concepts,
                "decisions": self.fingerprint.decisions,
                "questions": self.fingerprint.questions,
                "constraints": self.fingerprint.constraints,
                "watches": self.fingerprint.watches,
            },
            "version": self.version,
        }, indent=2)


def build_extraction_input(name: str, fingerprint: ModeFingerprint) -> str:
    """Build the input for the LLM primer extraction."""
    parts = [f"Mode name: {name}", ""]

    if fingerprint.concepts:
        parts.append("Concepts (what was understood):")
        for c in fingerprint.concepts:
            parts.append(f"  - {c}")
        parts.append("")

    if fingerprint.decisions:
        parts.append("Decisions (what was resolved):")
        for d in fingerprint.decisions:
            parts.append(f"  - {d}")
        parts.append("")

    if fingerprint.questions:
        parts.append("Open questions (what's still unresolved):")
        for q in fingerprint.questions:
            parts.append(f"  - {q}")
        parts.append("")

    if fingerprint.constraints:
        parts.append("Constraints (what must hold):")
        for c in fingerprint.constraints:
            parts.append(f"  - {c}")
        parts.append("")

    if fingerprint.watches:
        parts.append("Watches (what needs monitoring):")
        for w in fingerprint.watches:
            parts.append(f"  - {w}")
        parts.append("")

    if fingerprint.style_observations:
        parts.append(f"Communication style: {fingerprint.style_observations}")

    return "\n".join(parts)


def parse_extraction_output(raw: str, name: str, fingerprint: ModeFingerprint) -> ModePrimer:
    """Parse the LLM's primer extraction into a ModePrimer."""
    sections = {"primer": "", "boundary": "", "permissions": ""}
    current = None

    for line in raw.split("\n"):
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("## primer"):
            current = "primer"
            continue
        elif lower.startswith("## boundary"):
            current = "boundary"
            continue
        elif lower.startswith("## permission"):
            current = "permissions"
            continue
        if current and stripped:
            sections[current] += stripped + " "

    return ModePrimer(
        name=name,
        primer=sections["primer"].strip(),
        boundary=sections["boundary"].strip(),
        permissions=sections["permissions"].strip(),
        fingerprint=fingerprint,
    )
