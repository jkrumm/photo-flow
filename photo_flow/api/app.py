from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Photo-Flow Control Panel", version="0.4.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
