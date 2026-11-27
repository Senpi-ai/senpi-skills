"""The opening questions are asked in plain text: no skill text names the question-card tool."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8").read()
REF = open(os.path.join(HERE, "..", "references", "opening-questions.md"), encoding="utf-8").read()


def test_no_author_text_names_the_question_tool():
    assert "ask_user_question" not in SKILL
    assert "ask_user_question" not in REF
    assert "allow_text" not in SKILL + REF


def test_the_opening_questions_are_asked_in_plain_text_one_at_a_time():
    flat = " ".join(SKILL.split())
    assert "in plain text, one at a time, the second only after the first is answered" in flat
    assert "## 1. What matters most to you?" in REF
    assert "## 2. How hands-on do you want to be?" in REF
