from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from crewai import Crew, Process, Task

from . import config, prompts
from .agents import build_agents
from .memory import SessionMemory, StudentMemory
from .schemas import Evaluation, Quiz, QuestionResult, StudyPlan, json_guardrail, parse

AGENT_NAMES = {
    "coordinator": "Coordinator",
    "explainer": "Explainer",
    "quiz_master": "Quiz Master",
    "evaluator": "Evaluator",
    "student": "Student",
}


class AgentFailed(Exception):
    pass


class LeoEngine:
    """The agents and the Coordinator's logic, shared by the CLI and the Streamlit app.

    on_event(kind, data) lets an interface show handoffs and retries as they happen.
    """

    def __init__(self, on_event=None, verbose=False):
        self.on_event = on_event or (lambda kind, data: None)
        self.verbose = verbose
        self.llm = config.build_llm()
        self.agents = build_agents(self.llm)
        self.store = StudentMemory(config.MEMORY_FILE)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.mem = None
        self.plan = None
        self.lesson = ""
        self.scores = []
        self.strengths = []
        self.weak = []
        self.handoffs = []

    def handoff(self, src, dst, what):
        item = {"src": src, "dst": dst, "what": what}
        self.handoffs.append(item)
        self.on_event("handoff", item)

    def task(self, agent, description, expected, guardrail=None, context=None):
        kwargs = {"guardrail": guardrail, "guardrail_max_retries": 2} if guardrail else {}
        return Task(
            description=description,
            expected_output=expected,
            agent=self.agents[agent],
            context=context or [],
            **kwargs,
        )

    def run_crew(self, agent_keys, make_tasks, label):
        """Kick off a crew with a timeout and one retry so a stalled agent cannot hang the session.

        make_tasks builds the tasks from the current agents, so a retry after a stall
        runs on fresh agent instances instead of the one that is still busy.
        """
        last_error = None
        for attempt in range(2):
            tasks = make_tasks()
            crew = Crew(
                agents=[self.agents[k] for k in agent_keys],
                tasks=tasks,
                process=Process.sequential,
                verbose=self.verbose,
            )
            future = self.executor.submit(crew.kickoff)
            try:
                future.result(timeout=config.AGENT_TIMEOUT_SECONDS)
                return [t.output.raw for t in tasks]
            except FutureTimeout:
                last_error = f"{label} did not answer within {config.AGENT_TIMEOUT_SECONDS}s"
                self.executor = ThreadPoolExecutor(max_workers=1)
                self.agents = build_agents(self.llm)
            except Exception as err:
                last_error = f"{label} failed: {err}"
            if attempt == 0:
                self.on_event("retry", {"message": f"{last_error}. Asking the agent to try again."})
        raise AgentFailed(last_error)

    def run_one(self, agent, description, expected, guardrail=None):
        make = lambda: [self.task(agent, description, expected, guardrail)]  # noqa: E731
        return self.run_crew([agent], make, AGENT_NAMES[agent])[0]

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    def start_student(self, name):
        returning = self.store.is_returning(name)
        self.mem = SessionMemory(name, self.store.summary(name))
        if returning:
            return f"Welcome back, {name}. Here is what I remember:\n\n{self.mem.long_term_summary}"
        return f"Nice to meet you, {name}. I will plan your session and bring in the right teammate."

    def make_plan(self, request):
        self.handoff("student", "coordinator", "learning request")
        raw = self.run_one(
            "coordinator",
            prompts.COORDINATOR_PLAN.substitute(request=request, memory=self.mem.render()),
            "A JSON study plan.",
            json_guardrail(StudyPlan),
        )
        plan = parse(StudyPlan, raw)
        if plan.status == "unclear":
            self.mem.add("Coordinator", f"asked to clarify: {plan.clarifying_question}")
        return plan

    def accept_plan(self, plan):
        self.plan = plan
        self.mem.topic, self.mem.level = plan.topic, plan.level
        self.mem.add("Coordinator", f"planned '{plan.topic}' at {plan.level} level")

    def teach(self, instruction="Teach it for the first time."):
        self.handoff("coordinator", "explainer", "study plan (topic, level, goals, concepts)")
        try:
            self.lesson = self.run_one(
                "explainer",
                prompts.EXPLAINER_LESSON.substitute(
                    topic=self.plan.topic,
                    level=self.plan.level,
                    goals="; ".join(self.plan.learning_goals),
                    concepts=", ".join(self.plan.focus_concepts),
                    instruction=instruction,
                    memory=self.mem.render(),
                ),
                "A short Markdown lesson.",
            )
        except AgentFailed:
            self.lesson = "Goals: " + "; ".join(self.plan.learning_goals)
            raise
        self.mem.add("Explainer", "taught the lesson")
        return self.lesson

    def change_level(self, direction):
        self.plan.level = "advanced" if direction == "harder" else "beginner"
        self.mem.level = self.plan.level
        self.mem.add("Student", f"asked for a {direction} lesson")
        self.handoff("student", "coordinator", f"request: {direction}")
        return self.teach(instruction=f"The student asked for a {direction} version. Change the depth accordingly.")

    def answer_question(self, question):
        self.handoff("student", "explainer", "question")
        answer = self.run_one(
            "explainer",
            prompts.EXPLAINER_ANSWER.substitute(
                topic=self.plan.topic, question=question, lesson=self.lesson, level=self.plan.level
            ),
            "A short answer.",
        )
        self.mem.add("Student", f"asked: {question}")
        return answer

    def make_quiz(self, concepts, count):
        self.handoff("explainer", "quiz_master", "lesson text + concepts to test")
        raw = self.run_one(
            "quiz_master",
            prompts.QUIZ_MASTER.substitute(
                topic=self.plan.topic,
                level=self.plan.level,
                count=count,
                concepts=", ".join(concepts),
                lesson=self.lesson,
            ),
            "A JSON quiz.",
            json_guardrail(Quiz),
        )
        quiz = parse(Quiz, raw)
        self.mem.add("Quiz Master", f"wrote {len(quiz.questions)} questions")
        return quiz

    def reteach_and_quiz(self, weak, feedback, count):
        """One crew: the Explainer re-teaches, and its output is passed as context to the Quiz Master."""
        self.handoff("evaluator", "coordinator", f"weak concepts: {', '.join(weak)}")
        self.handoff("coordinator", "explainer", "re-teach weak concepts with the Evaluator's feedback")

        def make_tasks():
            reteach = self.task(
                "explainer",
                prompts.EXPLAINER_RETEACH.substitute(
                    weak=", ".join(weak), topic=self.plan.topic, level=self.plan.level, feedback=feedback
                ),
                "A short Markdown re-teaching of the weak concepts.",
            )
            quiz_task = self.task(
                "quiz_master",
                prompts.QUIZ_MASTER.substitute(
                    topic=self.plan.topic,
                    level=self.plan.level,
                    count=count,
                    concepts=", ".join(weak),
                    lesson="(the re-teaching from the Explainer is given in your context)",
                ),
                "A JSON quiz.",
                guardrail=json_guardrail(Quiz),
                context=[reteach],
            )
            return [reteach, quiz_task]

        lesson, quiz_raw = self.run_crew(["explainer", "quiz_master"], make_tasks, "Re-teach crew")
        self.lesson = lesson
        self.handoff("explainer", "quiz_master", "re-teaching passed as task context")
        self.mem.add("Explainer", f"re-taught {', '.join(weak)}")
        quiz = parse(Quiz, quiz_raw)
        self.mem.add("Quiz Master", f"wrote {len(quiz.questions)} follow-up questions")
        return lesson, quiz

    def evaluate(self, quiz, answers):
        """Returns (evaluation, used_fallback)."""
        self.handoff("quiz_master", "evaluator", "questions + answer key")
        self.handoff("student", "evaluator", "student answers")
        try:
            raw = self.run_one(
                "evaluator",
                prompts.EVALUATOR.substitute(
                    topic=self.plan.topic,
                    level=self.plan.level,
                    quiz=quiz.model_dump_json(indent=1),
                    answers="\n".join(f"{k}: {v}" for k, v in answers.items()),
                    pass_mark=config.PASS_MARK,
                ),
                "A JSON evaluation.",
                json_guardrail(Evaluation),
            )
            evaluation, fallback = parse(Evaluation, raw), False
        except AgentFailed:
            evaluation, fallback = self.fallback_grade(quiz, answers), True
        self.scores.append(evaluation.overall_score)
        self.strengths = sorted(set(self.strengths) | set(evaluation.strengths))
        self.weak = evaluation.weak_concepts
        self.mem.add(
            "Evaluator",
            f"score {evaluation.overall_score:.0%}, weak: {', '.join(evaluation.weak_concepts) or 'none'}",
        )
        return evaluation, fallback

    def fallback_grade(self, quiz, answers):
        results, weak = [], []
        for q in quiz.questions:
            given = answers.get(q.id, "").strip().upper()[:1]
            ok = q.type == "mcq" and given == q.answer.strip().upper()[:1]
            results.append(QuestionResult(id=q.id, correct=ok, score=1.0 if ok else 0.0, feedback=q.explanation))
            if not ok:
                weak.append(q.concept)
        score = sum(r.score for r in results) / max(len(results), 1)
        return Evaluation(
            results=results,
            overall_score=score,
            weak_concepts=sorted(set(weak)),
            summary="Graded by answer key only.",
            recommend_reteach=score < config.PASS_MARK,
        )

    def decide(self, evaluation, round_no):
        """The Coordinator's routing decision after grading: pass, limit or reteach."""
        needs_help = evaluation.recommend_reteach or evaluation.overall_score < config.PASS_MARK
        if not (needs_help and evaluation.weak_concepts):
            return "pass"
        if round_no >= config.MAX_RETEACH_ROUNDS:
            return "limit"
        return "reteach"

    @staticmethod
    def followup_count(evaluation):
        return min(3, max(2, len(evaluation.weak_concepts)))

    @staticmethod
    def mistakes(evaluation):
        return "\n".join(r.feedback for r in evaluation.results if r.score < 1)

    def wrap_up(self):
        self.handoff("evaluator", "coordinator", "scores, strengths and weak concepts")
        try:
            message = self.run_one(
                "coordinator",
                prompts.COORDINATOR_WRAP_UP.substitute(
                    student=self.mem.student,
                    topic=self.plan.topic,
                    scores=", ".join(f"{s:.0%}" for s in self.scores) or "no quiz taken",
                    strengths=", ".join(self.strengths) or "none recorded",
                    weak=", ".join(self.weak) or "none",
                    memory=self.mem.render(),
                ),
                "A short closing message.",
            )
        except AgentFailed:
            message = f"Well done today, {self.mem.student}. See you next time."
        self.save()
        return message

    def save(self):
        if self.plan:
            self.store.record_session(
                self.mem.student, self.plan.topic, self.plan.level, self.scores[-1] if self.scores else 0.0, self.weak
            )
