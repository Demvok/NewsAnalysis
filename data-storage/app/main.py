from fastapi import FastAPI
from sqlalchemy import create_engine, text
import os

app = FastAPI()

engine = create_engine(os.environ["DATABASE_URL"])


@app.get("/health")
def health():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": result.scalar()}