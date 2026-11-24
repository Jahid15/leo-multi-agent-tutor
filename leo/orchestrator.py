from . import config, ui
from .engine import AgentFailed, LeoEngine


class LeoSession:
    """Terminal interface: walks the student through the engine's steps."""

    def __init__(self, verbose=False):
        self.engine = LeoEngine(on_event=self.on_event, verbose=verbose)

    def on_event(self, kind, data):
        if kind == "handoff":
            ui.handoff(data["src"], data["dst"], data["what"])
        elif kind == "retry":
            ui.say("coordinator", data["message"])

    def start(self):
        ui.banner()
        name = ""
        while not name:
            name = ui.ask("What is your name?")
        ui.say("coordinator", self.engine.start_student(name))
        try:
            if not self.make_plan():
                return
            self.teach()
            self.quiz_round()
            self.wrap_up()
        except (KeyboardInterrupt, EOFError):
            ui.warn("Session stopped by the student.")
            self.save()
        finally:
            self.engine.close()

    def make_plan(self):
        ui.section("1. Coordinator plans the session")
        request = ""
        for _ in range(4):
            while not request:
                request = ui.ask("What would you like to learn today?")
            try:
                with ui.working("coordinator", "reading your request"):
                    plan = self.engine.make_plan(request)
            except AgentFailed as err:
                ui.warn(str(err))
                ui.say("coordinator", "I could not plan that right now. Could you say the topic in a few words?")
                request = ""
                continue

            if plan.status == "unclear":
                ui.say("coordinator", plan.clarifying_question or "Could you be more specific?")
                answer = ui.ask("Your answer")
                request = f"{request}. Clarification from the student: {answer}"
                continue

            ui.say_table("coordinator", ui.plan_table(plan), "study plan")
            ui.say("coordinator", plan.note_to_student or "Let us begin.")
            change = ui.ask("Press Enter to start, or type a change to the plan (for example: make it harder)", default="")
            if change:
                request = f"{request}. Change requested by the student: {change}"
                continue

            self.engine.accept_plan(plan)
            return True

        ui.say("coordinator", "Let us try again another time with a specific topic, for example 'photosynthesis' or 'Python lists'.")
        return False

    def show_lesson(self, run, status):
        try:
            with ui.working("explainer", status):
                lesson = run()
        except AgentFailed as err:
            ui.warn(str(err))
            ui.say("coordinator", "The Explainer is not responding, so I will move you to the quiz with the plan as your guide.")
            return False
        ui.say("explainer", lesson, title=self.engine.plan.topic, markdown=True)
        return True

    def teach(self):
        ui.section("2. Explainer teaches")
        if not self.show_lesson(self.engine.teach, "writing your lesson"):
            return
        while True:
            reply = ui.ask(
                "Press Enter for the quiz, ask the Explainer a question, or type simpler / harder",
                default="",
            )
            if not reply:
                return
            word = reply.lower()
            if word in {"simpler", "easier", "harder"}:
                direction = "harder" if word == "harder" else "simpler"
                ui.say("coordinator", f"Changing the level and asking the Explainer to teach a {direction} version.")
                if not self.show_lesson(lambda: self.engine.change_level(direction), "rewriting your lesson"):
                    return
                continue
            try:
                with ui.working("explainer", "answering"):
                    answer = self.engine.answer_question(reply)
                ui.say("explainer", answer, markdown=True)
            except AgentFailed as err:
                ui.warn(str(err))
                ui.say("coordinator", "The Explainer could not answer that. Let us keep going.")

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

    def quiz_round(self, round_no=0, prev_eval=None):
        engine = self.engine
        ui.section("3. Quiz Master quizzes" if round_no == 0 else f"Feedback loop, round {round_no}")
        try:
            if round_no == 0:
                with ui.working("quiz_master", "writing your quiz"):
                    quiz = engine.make_quiz(engine.plan.focus_concepts, engine.plan.num_questions)
            else:
                with ui.working("explainer", "re-teaching, then the Quiz Master writes new questions"):
                    lesson, quiz = engine.reteach_and_quiz(
                        prev_eval.weak_concepts, engine.mistakes(prev_eval), engine.followup_count(prev_eval)
                    )
                ui.say("explainer", lesson, title="re-teach", markdown=True)
        except AgentFailed as err:
            ui.warn(str(err))
            ui.say("coordinator", "I could not get a quiz ready, so we will finish here for today.")
            return
        answers = self.ask_quiz(quiz)

        ui.section("4. Evaluator gives feedback")
        with ui.working("evaluator", "grading your answers"):
            evaluation, fallback = engine.evaluate(quiz, answers)
        if fallback:
            ui.say("coordinator", "The Evaluator is not responding, so I graded the multiple choice questions with the answer key.")
        ui.say_table("evaluator", ui.results_table(quiz, evaluation, answers), f"score {evaluation.overall_score:.0%}")
        ui.say("evaluator", evaluation.summary)

        decision = engine.decide(evaluation, round_no)
        if decision == "pass":
            ui.say("coordinator", "Great work. No re-teaching needed.")
            return
        if decision == "limit":
            ui.say("coordinator", "We have practised these enough for one session. I will note them for next time.")
            return
        ui.say(
            "coordinator",
            f"The Evaluator flagged: {', '.join(evaluation.weak_concepts)}. "
            "I will send these back to the Explainer for a new explanation and a short follow-up quiz.",
        )
        if ui.ask("Press Enter to continue, or type skip to finish now", default="").lower() == "skip":
            engine.mem.add("Student", "skipped re-teaching")
            return
        self.quiz_round(round_no + 1, evaluation)

    def wrap_up(self):
        ui.section("5. Coordinator wraps up")
        with ui.working("coordinator", "writing your summary"):
            message = self.engine.wrap_up()
        ui.say("coordinator", message)
        ui.console.print(f"[dim]Saved progress for {self.engine.mem.student} to {config.MEMORY_FILE}[/]")

    def save(self):
        if self.engine.plan:
            self.engine.save()
            ui.console.print(f"[dim]Saved progress for {self.engine.mem.student} to {config.MEMORY_FILE}[/]")
