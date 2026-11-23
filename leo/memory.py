import json
from datetime import datetime
from pathlib import Path


class StudentMemory:
    """Long term memory of each student, saved to a JSON file between sessions."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2), "utf-8")

    def profile(self, name):
        key = name.strip().lower()
        if key not in self.data:
            self.data[key] = {"name": name.strip(), "sessions": [], "weak_concepts": []}
        return self.data[key]

    def is_returning(self, name):
        return bool(self.data.get(name.strip().lower(), {}).get("sessions"))

    def summary(self, name):
        p = self.profile(name)
        if not p["sessions"]:
            return f"{p['name']} is a new student. No previous sessions."
        recent = p["sessions"][-3:]
        lines = [f"{p['name']} has studied {len(p['sessions'])} topic(s) with Leo."]
        for s in recent:
            lines.append(f"- {s['topic']} ({s['level']}): final score {s['score']:.0%} on {s['date']}")
        if p["weak_concepts"]:
            lines.append("Concepts they found hard before: " + ", ".join(p["weak_concepts"][-8:]))
        return "\n".join(lines)

    def record_session(self, name, topic, level, score, weak_concepts):
        p = self.profile(name)
        p["sessions"].append(
            {
                "topic": topic,
                "level": level,
                "score": score,
                "date": datetime.now().strftime("%Y-%m-%d"),
            }
        )
        for concept in weak_concepts:
            if concept not in p["weak_concepts"]:
                p["weak_concepts"].append(concept)
        self.save()


class SessionMemory:
    """Short term memory shared by all agents during one session."""

    def __init__(self, student, long_term_summary):
        self.student = student
        self.long_term_summary = long_term_summary
        self.topic = ""
        self.level = ""
        self.events = []

    def add(self, agent, note):
        self.events.append(f"[{agent}] {note}")

    def render(self):
        recent = "\n".join(self.events[-12:]) or "Nothing yet."
        return (
            f"Student: {self.student}\n"
            f"Topic: {self.topic or 'not chosen yet'}\n"
            f"Level: {self.level or 'unknown'}\n"
            f"History with Leo:\n{self.long_term_summary}\n"
            f"This session so far:\n{recent}"
        )
