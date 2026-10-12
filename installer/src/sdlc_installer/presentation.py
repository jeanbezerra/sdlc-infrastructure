"""Saída humana em Rich; stdout JSON permanece um documento por comando."""

import json
from contextlib import nullcontext


class Output:
    def __init__(self, *, json_output=False, quiet=False, color="auto", redactor=None):
        from rich.console import Console

        self.json_output, self.quiet, self.redactor = json_output, quiet, redactor
        self.console = Console(
            force_terminal=True if color == "always" else False if color == "never" else None
        )

    def result(self, data, title="Resultado"):
        data = self.redactor.data(data) if self.redactor else data
        if self.json_output:
            print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
        else:
            from rich.panel import Panel
            from rich.text import Text

            self.console.print(Panel(Text(json.dumps(data, ensure_ascii=False, indent=2)), title=title))

    def table(self, rows, columns, *, title, data):
        if self.json_output:
            self.result(data)
            return
        from rich.table import Table

        table = Table(title=title)
        for column in columns:
            table.add_column(column, overflow="fold")
        for row in rows:
            table.add_row(*(self.redactor.text(item) if self.redactor else str(item) for item in row))
        self.console.print(table)

    def progress(self, label):
        if self.json_output or self.quiet or not self.console.is_terminal:
            return nullcontext()
        # O progresso vai para stderr, junto dos eventos, sem misturar stdout JSON.
        from rich.console import Console

        return Console(stderr=True).status(label)
