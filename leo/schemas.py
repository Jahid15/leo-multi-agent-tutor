import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class StudyPlan(BaseModel):
    status: Literal["ready", "unclear"]
    topic: str = ""
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    learning_goals: list[str] = Field(default_factory=list)
    focus_concepts: list[str] = Field(default_factory=list)
    num_questions: int = 4
    clarifying_question: str = ""
    note_to_student: str = ""


class QuizQuestion(BaseModel):
    id: int
    type: Literal["mcq", "short"]
    concept: str
    question: str
    options: list[str] = Field(default_factory=list)
    answer: str
    explanation: str


class Quiz(BaseModel):
    topic: str
    questions: list[QuizQuestion]


class QuestionResult(BaseModel):
    id: int
    correct: bool
    score: float
    feedback: str


class Evaluation(BaseModel):
    results: list[QuestionResult]
    overall_score: float
    weak_concepts: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    summary: str
    recommend_reteach: bool


def extract_json(text):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in the agent output.")
    return json.loads(text[start : end + 1])


def json_guardrail(model_cls):
    def guardrail(output):
        try:
            data = model_cls.model_validate(extract_json(output.raw))
        except (ValueError, ValidationError) as err:
            return False, f"Return only valid JSON matching the schema. Problem: {err}"
        return True, data.model_dump_json()

    return guardrail


def parse(model_cls, raw):
    return model_cls.model_validate(extract_json(raw))
