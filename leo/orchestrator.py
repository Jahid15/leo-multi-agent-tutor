from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from crewai import Crew, Process, Task

from . import config, prompts, ui
from .agents import build_agents
from .memory import SessionMemory, StudentMemory
from .schemas import Evaluation, Quiz, QuestionResult, StudyPlan, json_guardrail, parse


class AgentFailed(Exception):
    pass


class LeoSession:
    """Sequential pipeline run by the Coordinator with a feedback loop and human-in-the-loop steps."""

    def __init__(self, verbose=False):
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
                ui.say("coordinator", f"{last_error}. Asking the agent to try again.")
        raise AgentFailed(last_error)

    def run_one(self, agent, description, expected, status, guardrail=None):
        with ui.working(agent, status):
            make = lambda: [self.task(agent, description, expected, guardrail)]  # noqa: E731
            return self.run_crew([agent], make, ui.name_of(agent))[0]

    def start(self):
        ui.banner()
        name = ""
        while not name:
            name = ui.ask("What is your name?")
        returning = self.store.is_returning(name)
        self.mem = SessionMemory(name, self.store.summary(name))
        if returning:
            ui.say("coordinator", f"Welcome back, {name}. Here is what I remember:\n\n{self.mem.long_term_summary}")
        else:
            ui.say("coordinator", f"Nice to meet you, {name}. I will plan your session and bring in the right teammate.")

        try:
            if not self.make_plan():
                return
            self.teach()
            self.quiz_round(self.plan.focus_concepts, self.plan.num_questions, first=True)
            self.wrap_up()
        except (KeyboardInterrupt, EOFError):
            ui.warn("Session stopped by the student.")
            self.save()
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)

    def make_plan(self):
        ui.section("1. Coordinator plans the session")
        request = ""
        for _ in range(4):
            while not request:
                request = ui.ask("What would you like to learn today?")
            try:
                raw = self.run_one(
                    "coordinator",
                    prompts.COORDINATOR_PLAN.substitute(request=request, memory=self.mem.render()),
                    "A JSON study plan.",
                    "reading your request",
                    json_guardrail(StudyPlan),
                )
                plan = parse(StudyPlan, raw)
            except AgentFailed as err:
                ui.warn(str(err))
                ui.say("coordinator", "I could not plan that right now. Could you say the topic in a few words?")
                request = ""
                continue

            if plan.status == "unclear":
                ui.say("coordinator", plan.clarifying_question or "Could you be more specific?")
                self.mem.add("Coordinator", f"asked to clarify: {plan.clarifying_question}")
                answer = ui.ask("Your answer")
                request = f"{request}. Clarification from the student: {answer}"
                continue

            ui.say_table("coordinator", ui.plan_table(plan), "study plan")
            ui.say("coordinator", plan.note_to_student or "Let us begin.")
            change = ui.ask("Press Enter to start, or type a change to the plan (for example: make it harder)", default="")
            if change:
                request = f"{request}. Change requested by the student: {change}"
                continue

            self.plan = plan
            self.mem.topic, self.mem.level = plan.topic, plan.level
            self.mem.add("Coordinator", f"planned '{plan.topic}' at {plan.level} level")
            return True

        ui.say("coordinator", "Let us try again another time with a specific topic, for example 'photosynthesis' or 'Python lists'.")
        return False

    def teach(self, instruction="Teach it for the first time."):
        ui.section("2. Explainer teaches")
        ui.handoff("coordinator", "explainer", "study plan (topic, level, goals, concepts)")
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
                "writing your lesson",
            )
        except AgentFailed as err:
            ui.warn(str(err))
            ui.say("coordinator", "The Explainer is not responding, so I will move you to the quiz with the plan as your guide.")
            self.lesson = "Goals: " + "; ".join(self.plan.learning_goals)
            return
        ui.say("explainer", self.lesson, title=self.plan.topic, markdown=True)
        self.mem.add("Explainer", "taught the lesson")
        self.student_checkpoint()

    def student_checkpoint(self):
        while True:
            reply = ui.ask(
                "Press Enter for the quiz, ask the Explainer a question, or type simpler / harder",
                default="",
            )
            if not reply:
                return
            if reply.lower() in {"simpler", "easier", "harder"}:
                self.plan.level = {"simpler": "beginner", "easier": "beginner"}.get(reply.lower(), "advanced")
                self.mem.level = self.plan.level
                self.mem.add("Student", f"asked for a {reply.lower()} lesson")
                ui.handoff("student", "coordinator", f"request: {reply.lower()}")
                ui.say("coordinator", f"Switching the level to {self.plan.level} and asking the Explainer to teach again.")
                self.teach(instruction=f"The student asked for a {reply.lower()} version. Change the depth accordingly.")
                return
            ui.handoff("student", "explainer", "question")
            try:
                answer = self.run_one(
                    "explainer",
                    prompts.EXPLAINER_ANSWER.substitute(
                        topic=self.plan.topic, question=reply, lesson=self.lesson, level=self.plan.level
                    ),
                    "A short answer.",
                    "answering",
                )
                ui.say("explainer", answer, markdown=True)
                self.mem.add("Student", f"asked: {reply}")
            except AgentFailed as err:
                ui.warn(str(err))
                ui.say("coordinator", "The Explainer could not answer that. Let us keep going.")

    def make_quiz(self, concepts, count):
        ui.handoff("explainer", "quiz_master", "lesson text + concepts to test")
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
            "writing your quiz",
            json_guardrail(Quiz),
        )
        return parse(Quiz, raw)

    def reteach_and_quiz(self, weak, feedback, count):
        """One crew: the Explainer re-teaches, and its output is passed as context to the Quiz Master."""
        ui.handoff("evaluator", "coordinator", f"weak concepts: {', '.join(weak)}")
        ui.handoff("coordinator", "explainer", "re-teach weak concepts with the Evaluator's feedback")

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

        with ui.working("explainer", "re-teaching, then the Quiz Master writes new questions"):
            lesson, quiz_raw = self.run_crew(["explainer", "quiz_master"], make_tasks, "Re-teach crew")
        self.lesson = lesson
        ui.say("explainer", lesson, title="re-teach", markdown=True)
        ui.handoff("explainer", "quiz_master", "re-teaching passed as task context")
        self.mem.add("Explainer", f"re-taught {', '.join(weak)}")
        return parse(Quiz, quiz_raw)

    def ask_quiz(self, quiz):
        answers = {}
        for q in quiz.questions:
            body = q.question
            if q.options:
                body += "\n\n" + "\n".join(q.options)
            ui.say("quiz_master", body, title=f"Question {q.id} ({q.concept})")
            hint = "letter" if q.type == "mcq" else "short answer"
            answers[q.id] = ui.ask(f"Your {hint}", default="") or "(no answer)"
        return answers

    def evaluate(self, quiz, answers):
        ui.handoff("quiz_master", "evaluator", "questions + answer key")
        ui.handoff("student", "evaluator", "student answers")
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
                "grading your answers",
                json_guardrail(Evaluation),
            )
            return parse(Evaluation, raw)
        except AgentFailed as err:
            ui.warn(str(err))
            ui.say("coordinator", "The Evaluator is not responding, so I graded the multiple choice questions with the answer key.")
            return self.fallback_grade(quiz, answers)

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

    def quiz_round(self, concepts, count, first=False, round_no=0, prev_eval=None):
        ui.section("3. Quiz Master quizzes" if first else f"Feedback loop, round {round_no}")
        try:
            if first:
                quiz = self.make_quiz(concepts, count)
            else:
                feedback = "\n".join(r.feedback for r in prev_eval.results if r.score < 1)
                quiz = self.reteach_and_quiz(concepts, feedback, count)
        except AgentFailed as err:
            ui.warn(str(err))
            ui.say("coordinator", "I could not get a quiz ready, so we will finish here for today.")
            return
        self.mem.add("Quiz Master", f"wrote {len(quiz.questions)} questions")
        answers = self.ask_quiz(quiz)

        ui.section("4. Evaluator gives feedback")
        evaluation = self.evaluate(quiz, answers)
        ui.say_table("evaluator", ui.results_table(quiz, evaluation, answers), f"score {evaluation.overall_score:.0%}")
        ui.say("evaluator", evaluation.summary)
        self.scores.append(evaluation.overall_score)
        self.strengths = sorted(set(self.strengths) | set(evaluation.strengths))
        self.weak = evaluation.weak_concepts
        self.mem.add("Evaluator", f"score {evaluation.overall_score:.0%}, weak: {', '.join(evaluation.weak_concepts) or 'none'}")

        needs_help = evaluation.recommend_reteach or evaluation.overall_score < config.PASS_MARK
        if not (needs_help and evaluation.weak_concepts):
            ui.say("coordinator", "Great work. No re-teaching needed.")
            return
        if round_no >= config.MAX_RETEACH_ROUNDS:
            ui.say("coordinator", "We have practised these enough for one session. I will note them for next time.")
            return

        ui.say(
            "coordinator",
            f"The Evaluator flagged: {', '.join(evaluation.weak_concepts)}. "
            "I will send these back to the Explainer for a new explanation and a short follow-up quiz.",
        )
        if ui.ask("Press Enter to continue, or type skip to finish now", default="").lower() == "skip":
            self.mem.add("Student", "skipped re-teaching")
            return
        self.quiz_round(
            evaluation.weak_concepts,
            min(3, max(2, len(evaluation.weak_concepts))),
            round_no=round_no + 1,
            prev_eval=evaluation,
        )

    def wrap_up(self):
        ui.section("5. Coordinator wraps up")
        ui.handoff("evaluator", "coordinator", "scores, strengths and weak concepts")
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
                "writing your summary",
            )
        except AgentFailed:
            message = f"Well done today, {self.mem.student}. See you next time."
        ui.say("coordinator", message)
        self.save()

    def save(self):
        if self.plan:
            self.store.record_session(
                self.mem.student, self.plan.topic, self.plan.level, self.scores[-1] if self.scores else 0.0, self.weak
            )
            ui.console.print(f"[dim]Saved progress for {self.mem.student} to {config.MEMORY_FILE}[/]")
