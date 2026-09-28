from fastapi import FastAPI
import logging
from app import LLM

app = FastAPI(
    title="NewsAnalysis-AnalysisLayer",
    description="Full API for NewsAnalysis data analysis",
    version="0.0.1"
)

logger = logging.getLogger("data-analysis")

@app.get("/health", summary="Health check")
async def root():
    return {"message": "Analysis API Server is running", "version": app.version}

@app.post("/llm", summary="Test LLM connection")
def test_llm_connection(query: str):
    response = LLM.llm_invoke(query)
    return {"message": "LLM connection is successful", "response": response}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)