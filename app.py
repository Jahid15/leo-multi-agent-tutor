import os

os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

import html  # noqa: E402

import streamlit as st  # noqa: E402

from leo import config  # noqa: E402
from leo.engine import AGENT_NAMES, AgentFailed, LeoEngine  # noqa: E402

st.set_page_config(page_title="Leo, multi-agent tutor", page_icon="🎓", layout="wide")

COLORS = {
    "student": "#64748b",
    "coordinator": "#a855f7",
    "explainer": "#0ea5e9",
    "quiz_master": "#f59e0b",
    "evaluator": "#22c55e",
}
AVATARS = {
    "student": "🙂",
    "coordinator": "🧭",
    "explainer": "📘",
    "quiz_master": "❓",
    "evaluator": "✅",
}
PIPELINE = ["student", "coordinator", "explainer", "quiz_master", "evaluator"]
ACTION_AGENT = {
    "plan": "coordinator",
    "teach": "explainer",
    "question": "explainer",
    "level": "explainer",
    "quiz": "quiz_master",
    "evaluate": "evaluator",
    "reteach": "explainer",
    "wrap": "coordinator",
}
ACTION_STATUS = {
    "plan": "Coordinator is reading your request",
    "teach": "Explainer is writing your lesson",
    "question": "Explainer is answering your question",
    "level": "Explainer is rewriting the lesson",
    "quiz": "Quiz Master is writing your quiz",
    "evaluate": "Evaluator is grading your answers",
    "reteach": "Explainer is re-teaching, then the Quiz Master writes new questions",
    "wrap": "Coordinator is writing your summary",
}

CSS = """
<style>
.leo-pipe {display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; margin:.25rem 0 1rem 0;}
.leo-node {border:2px solid var(--c); border-radius:10px; padding:.45rem .8rem; font-size:.9rem;
           color:var(--c); background:transparent; min-width:7.5rem; text-align:center;}
.leo-node.active {background:var(--c); color:white; box-shadow:0 0 0 4px color-mix(in srgb, var(--c) 30%, transparent);
                  animation:leo-pulse 1.2s ease-in-out infinite;}
.leo-node small {display:block; font-size:.7rem; opacity:.85;}
.leo-arrow {color:#94a3b8;}
@keyframes leo-pulse {50% {box-shadow:0 0 0 8px color-mix(in srgb, var(--c) 10%, transparent);}}
.leo-badge {display:inline-block; padding:.1rem .55rem; border-radius:999px; color:white; font-size:.8rem;
            margin-bottom:.35rem;}
.leo-handoff {text-align:center; font-size:.85rem; color:#64748b; margin:.2rem 0 .6rem 0;}
.leo-handoff b {font-weight:600;}
.leo-log {font-size:.8rem; line-height:1.5;}
[data-testid="stElementContainer"][data-stale="true"] {display:none;}
</style>
"""


def badge(agent, extra=""):
    label = AGENT_NAMES[agent] + (f"  ·  {extra}" if extra else "")
    return f'<span class="leo-badge" style="background:{COLORS[agent]}">{html.escape(label)}</span>'


def agent_html(agent):
    return f'<b style="color:{COLORS[agent]}">{AGENT_NAMES[agent]}</b>'


def init_state():
    s = st.session_state
    if "engine" in s:
        return
    s.engine = LeoEngine(on_event=on_event)
    s.feed = []
    s.stage = "name"
    s.pending = None
    s.request = ""
    s.attempts = 0
    s.plan_draft = None
    s.quiz = None
    s.round_no = 0
    s.last_eval = None


def on_event(kind, data):
    if kind == "handoff":
        st.session_state.feed.append({"type": "handoff", **data})
    elif kind == "retry":
        add_msg("coordinator", data["message"])


def add_msg(agent, text, title="", md=True):
    st.session_state.feed.append({"type": "msg", "agent": agent, "text": text, "title": title, "md": md})


def queue(action, *args):
    st.session_state.pending = (action, args)
    st.rerun()


def render_pipeline(active):
    parts = []
    for i, key in enumerate(PIPELINE):
        cls = "leo-node active" if key == active else "leo-node"
        note = "<small>working...</small>" if key == active else "<small>&nbsp;</small>"
        parts.append(f'<div class="{cls}" style="--c:{COLORS[key]}">{AGENT_NAMES[key]}{note}</div>')
        if i < len(PIPELINE) - 1:
            parts.append('<span class="leo-arrow">→</span>')
    st.markdown(f'<div class="leo-pipe">{"".join(parts)}</div>', unsafe_allow_html=True)


def render_feed():
    for item in st.session_state.feed:
        kind = item["type"]
        if kind == "handoff":
            st.markdown(
                f'<div class="leo-handoff">{agent_html(item["src"])} → {agent_html(item["dst"])}'
                f"  ·  handoff: {html.escape(item['what'])}</div>",
                unsafe_allow_html=True,
            )
            continue
        agent = item["agent"]
        with st.chat_message(AGENT_NAMES[agent], avatar=AVATARS[agent]):
            st.markdown(badge(agent, item.get("title", "")), unsafe_allow_html=True)
            if kind == "msg":
                if item["md"]:
                    st.markdown(item["text"])
                else:
                    st.markdown(html.escape(item["text"]).replace("\n", "  \n"))
            elif kind == "plan":
                p = item["plan"]
                st.markdown(f"Topic: {p['topic']}  \nLevel: {p['level']}  \nQuestions: {p['num_questions']}")
                st.markdown("Goals:\n" + "\n".join(f"- {g}" for g in p["learning_goals"]))
                st.markdown("Concepts: " + ", ".join(p["focus_concepts"]))
                if p.get("note_to_student"):
                    st.caption(p["note_to_student"])
            elif kind == "results":
                st.metric("Score", f"{item['score']:.0%}")
                st.table(item["rows"])
                st.markdown(item["summary"])


def render_sidebar():
    engine = st.session_state.engine
    with st.sidebar:
        st.header("Leo")
        st.caption(f"Model: {config.MODEL}")
        st.caption(f"Pass mark: {config.PASS_MARK:.0%}  ·  Re-teach rounds: {config.MAX_RETEACH_ROUNDS}")
        st.subheader("Agents")
        for key in PIPELINE[1:]:
            st.markdown(f"{AVATARS[key]} {agent_html(key)}", unsafe_allow_html=True)
        if engine.mem:
            st.subheader("Memory")
            st.text(engine.mem.render())
        st.subheader("Handoff log")
        if engine.handoffs:
            lines = [
                f"{i}. {agent_html(h['src'])} → {agent_html(h['dst'])}: {html.escape(h['what'])}"
                for i, h in enumerate(engine.handoffs, 1)
            ]
            st.markdown(f'<div class="leo-log">{"<br>".join(lines)}</div>', unsafe_allow_html=True)
        else:
            st.caption("No handoffs yet.")
        if st.button("New session", use_container_width=True):
            engine.close()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


def letter_of(option, index):
    head = option.strip()[:1].upper()
    return head if head.isalpha() and option.strip()[1:2] in {")", ".", ":"} else "ABCDEFGH"[index]


def results_rows(quiz, evaluation, answers):
    by_id = {q.id: q for q in quiz.questions}
    rows = []
    for r in evaluation.results:
        q = by_id.get(r.id)
        rows.append(
            {
                "#": r.id,
                "Question": q.question if q else "",
                "Your answer": answers.get(r.id, ""),
                "Key": q.answer if q else "",
                "Score": f"{r.score:g}",
                "Feedback": r.feedback,
            }
        )
    return rows


def run_pending():
    s = st.session_state
    action, args = s.pending
    s.pending = None
    engine = s.engine
    with st.spinner(ACTION_STATUS[action] + "..."):
        try:
            handle(action, args, engine)
        except AgentFailed as err:
            recover(action, str(err))
    st.rerun()


def handle(action, args, engine):
    s = st.session_state
    if action == "plan":
        plan = engine.make_plan(s.request)
        s.attempts += 1
        if plan.status == "unclear":
            add_msg("coordinator", plan.clarifying_question or "Could you be more specific?", "clarifying")
            s.stage = "clarify" if s.attempts < 4 else "request"
            if s.attempts >= 4:
                add_msg("coordinator", "Let us start again with a specific topic, for example 'photosynthesis' or 'Python lists'.")
                s.request, s.attempts = "", 0
            return
        s.plan_draft = plan
        s.feed.append({"type": "plan", "agent": "coordinator", "title": "study plan", "plan": plan.model_dump()})
        s.stage = "plan_review"
    elif action == "teach":
        lesson = engine.teach()
        add_msg("explainer", lesson, engine.plan.topic)
        s.stage = "lesson"
    elif action == "question":
        add_msg("explainer", engine.answer_question(args[0]), "answer")
    elif action == "level":
        add_msg("coordinator", f"Asking the Explainer for a {args[0]} version of the lesson.")
        add_msg("explainer", engine.change_level(args[0]), f"{args[0]} version")
    elif action == "quiz":
        s.quiz = engine.make_quiz(engine.plan.focus_concepts, engine.plan.num_questions)
        add_msg("quiz_master", f"I wrote {len(s.quiz.questions)} questions from the lesson. Answer them below.", "quiz")
        s.stage = "quiz"
    elif action == "evaluate":
        answers = args[0]
        evaluation, fallback = engine.evaluate(s.quiz, answers)
        if fallback:
            add_msg("coordinator", "The Evaluator is not responding, so I graded the multiple choice questions with the answer key.")
        s.feed.append(
            {
                "type": "results",
                "agent": "evaluator",
                "title": "feedback",
                "score": evaluation.overall_score,
                "rows": results_rows(s.quiz, evaluation, answers),
                "summary": evaluation.summary,
            }
        )
        s.last_eval = evaluation
        decision = engine.decide(evaluation, s.round_no)
        if decision == "reteach":
            add_msg(
                "coordinator",
                f"The Evaluator flagged: {', '.join(evaluation.weak_concepts)}. "
                "I will send these back to the Explainer for a new explanation and a short follow-up quiz.",
                "feedback loop",
            )
            s.stage = "reteach_offer"
        else:
            note = (
                "Great work. No re-teaching needed."
                if decision == "pass"
                else "We have practised these enough for one session. I will note them for next time."
            )
            add_msg("coordinator", note)
            s.pending = ("wrap", ())
    elif action == "reteach":
        ev = s.last_eval
        s.round_no += 1
        lesson, s.quiz = engine.reteach_and_quiz(ev.weak_concepts, engine.mistakes(ev), engine.followup_count(ev))
        add_msg("explainer", lesson, f"re-teach, round {s.round_no}")
        add_msg("quiz_master", f"Here are {len(s.quiz.questions)} follow-up questions on what you found hard.", "follow-up quiz")
        s.stage = "quiz"
    elif action == "wrap":
        add_msg("coordinator", engine.wrap_up(), "summary")
        add_msg("coordinator", f"Progress saved for {engine.mem.student}.", md=False)
        s.stage = "done"


def recover(action, error):
    s = st.session_state
    add_msg("coordinator", f"{error}", "problem")
    if action == "plan":
        add_msg("coordinator", "I could not plan that right now. Could you say the topic in a few words?")
        s.stage, s.request = "request", ""
    elif action in {"teach", "level"}:
        add_msg("coordinator", "The Explainer is not responding, so we will go to the quiz with the plan as your guide.")
        s.pending, s.stage = ("quiz", ()), "lesson"
    elif action == "question":
        add_msg("coordinator", "The Explainer could not answer that. You can ask again or go to the quiz.")
    elif action in {"quiz", "reteach"}:
        add_msg("coordinator", "I could not get a quiz ready, so we will finish here for today.")
        s.pending = ("wrap", ())


def render_controls():
    s = st.session_state
    stage = s.stage
    if stage == "name":
        name = st.chat_input("What is your name?")
        if name and name.strip():
            name = name.strip()
            add_msg("student", name, md=False)
            add_msg("coordinator", s.engine.start_student(name), "welcome")
            add_msg("coordinator", "What would you like to learn today?")
            s.stage = "request"
            st.rerun()
    elif stage in {"request", "clarify"}:
        text = st.chat_input("Your answer" if stage == "clarify" else "What would you like to learn today?")
        if text and text.strip():
            add_msg("student", text.strip(), md=False)
            s.request = f"{s.request}. Clarification from the student: {text.strip()}" if stage == "clarify" else text.strip()
            queue("plan")
    elif stage == "plan_review":
        st.caption("Human in the loop: accept the plan, or type a change below.")
        if st.button("Start the lesson", type="primary"):
            s.engine.accept_plan(s.plan_draft)
            queue("teach")
        change = st.chat_input("Type a change to the plan, for example: make it harder")
        if change and change.strip():
            add_msg("student", change.strip(), md=False)
            s.request = f"{s.request}. Change requested by the student: {change.strip()}"
            queue("plan")
    elif stage == "lesson":
        st.caption("Human in the loop: ask the Explainer a question, change the level, or go to the quiz.")
        c1, c2, c3 = st.columns(3)
        if c1.button("Quiz me", type="primary", use_container_width=True):
            add_msg("student", "I am ready for the quiz.", md=False)
            queue("quiz")
        if c2.button("Simpler", use_container_width=True):
            add_msg("student", "Can you make it simpler?", md=False)
            queue("level", "simpler")
        if c3.button("Harder", use_container_width=True):
            add_msg("student", "Can you make it harder?", md=False)
            queue("level", "harder")
        question = st.chat_input("Ask the Explainer a question")
        if question and question.strip():
            add_msg("student", question.strip(), md=False)
            queue("question", question.strip())
    elif stage == "quiz":
        render_quiz_form()
    elif stage == "reteach_offer":
        st.caption("Human in the loop: let the team re-teach, or finish now.")
        c1, c2 = st.columns(2)
        if c1.button("Re-teach me", type="primary", use_container_width=True):
            add_msg("student", "Yes, please teach me again.", md=False)
            queue("reteach")
        if c2.button("Skip and finish", use_container_width=True):
            add_msg("student", "Skip, let us finish.", md=False)
            s.engine.mem.add("Student", "skipped re-teaching")
            queue("wrap")
    elif stage == "done":
        st.success("Session complete.")
        if st.button("Start a new session", type="primary"):
            s.engine.close()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


def render_quiz_form():
    s = st.session_state
    quiz = s.quiz
    with st.form(f"quiz_{s.round_no}"):
        st.markdown(badge("quiz_master", "questions"), unsafe_allow_html=True)
        picks = {}
        for q in quiz.questions:
            st.markdown(f"Q{q.id}. {q.question}")
            st.caption(f"Concept: {q.concept}")
            if q.type == "mcq" and q.options:
                choice = st.radio(
                    f"q{q.id}", q.options, index=None, key=f"r{s.round_no}_{q.id}", label_visibility="collapsed"
                )
                picks[q.id] = letter_of(choice, q.options.index(choice)) if choice else ""
            else:
                picks[q.id] = st.text_input(
                    f"q{q.id}", key=f"t{s.round_no}_{q.id}", placeholder="Your short answer", label_visibility="collapsed"
                )
        if st.form_submit_button("Submit answers to the Evaluator", type="primary"):
            answers = {k: (v.strip() if v else "") or "(no answer)" for k, v in picks.items()}
            summary = "\n".join(f"Q{k}: {v}" for k, v in answers.items())
            add_msg("student", summary, "my answers", md=False)
            queue("evaluate", answers)


def main():
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("Leo")
    st.caption("A multi-agent study assistant: Coordinator, Explainer, Quiz Master and Evaluator working together.")
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        st.error("ANTHROPIC_API_KEY is empty. Put your key in the .env file and restart the app.")
        st.stop()
    init_state()
    pending = st.session_state.pending
    render_pipeline(ACTION_AGENT.get(pending[0]) if pending else None)
    render_sidebar()
    render_feed()
    if pending:
        run_pending()
    else:
        render_controls()


main()
