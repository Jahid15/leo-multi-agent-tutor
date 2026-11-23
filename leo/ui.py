from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table

console = Console()

AGENT_STYLE = {
    "coordinator": ("Coordinator", "magenta"),
    "explainer": ("Explainer", "cyan"),
    "quiz_master": ("Quiz Master", "yellow"),
    "evaluator": ("Evaluator", "green"),
    "student": ("Student", "white"),
}


def name_of(agent):
    return AGENT_STYLE[agent][0]


def banner():
    console.print(
        Panel(
            "Leo, your multi-agent study assistant\n"
            "Coordinator  ->  Explainer  ->  Quiz Master  ->  Evaluator",
            style="bold blue",
            expand=False,
        )
    )


def say(agent, text, title=None, markdown=False):
    label, color = AGENT_STYLE[agent]
    body = Markdown(text) if markdown else text
    console.print(Panel(body, title=f"[bold]{label}[/]" + (f"  {title}" if title else ""), border_style=color))


def handoff(src, dst, what):
    _, c1 = AGENT_STYLE[src]
    _, c2 = AGENT_STYLE[dst]
    console.print(f"  [{c1}]{name_of(src)}[/]  ->  [{c2}]{name_of(dst)}[/]  [dim]handoff: {what}[/]")


def working(agent, what):
    label, color = AGENT_STYLE[agent]
    return console.status(f"[{color}]{label}[/] is {what}...", spinner="dots")


def section(text):
    console.print(Rule(f"[bold]{text}[/]"))


def ask(prompt, default=None):
    return Prompt.ask(f"[bold white]You[/] {prompt}", default=default, show_default=False).strip()


def warn(text):
    console.print(f"[bold red]![/] {text}")


def plan_table(plan):
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_row("[dim]Topic[/]", plan.topic)
    t.add_row("[dim]Level[/]", plan.level)
    t.add_row("[dim]Goals[/]", "\n".join(f"- {g}" for g in plan.learning_goals))
    t.add_row("[dim]Concepts[/]", ", ".join(plan.focus_concepts))
    t.add_row("[dim]Questions[/]", str(plan.num_questions))
    return t


def say_table(agent, table, title):
    label, color = AGENT_STYLE[agent]
    console.print(Panel(table, title=f"[bold]{label}[/]  {title}", border_style=color))


def results_table(quiz, evaluation, answers):
    by_id = {q.id: q for q in quiz.questions}
    t = Table(show_lines=True)
    t.add_column("#", width=3)
    t.add_column("Your answer")
    t.add_column("Key", width=12)
    t.add_column("Score", width=6)
    t.add_column("Feedback")
    for r in evaluation.results:
        q = by_id.get(r.id)
        mark = "[green]" if r.score >= 1 else "[yellow]" if r.score > 0 else "[red]"
        t.add_row(
            str(r.id),
            answers.get(r.id, ""),
            q.answer if q else "",
            f"{mark}{r.score:g}[/]",
            r.feedback,
        )
    return t
