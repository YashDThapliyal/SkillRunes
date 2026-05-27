"""Entry point for `python -m skillrunes` and the `skillrunes` console script."""


def main() -> None:
    """Invoke the Typer CLI application."""
    from skillrunes.cli import app
    app()


if __name__ == "__main__":
    main()
