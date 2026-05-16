from .app import create_app


def main() -> None:
    app = create_app()
    cfg = app.config["NUDGE_CONFIG"]
    app.run(host="0.0.0.0", port=cfg.port)


if __name__ == "__main__":
    main()
