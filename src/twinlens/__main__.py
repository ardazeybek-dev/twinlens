from .cli import main

if __name__ == "__main__":  # guard keeps worker processes (spawn) from re-running the CLI
    raise SystemExit(main())
