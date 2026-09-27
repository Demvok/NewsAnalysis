from fastapi import FastAPI
import os
import logging

app = FastAPI(
    title="NewsAnalysis-DataIngestion",
    description="Full API for NewsAnalysis data ingestion",
    version="0.0.1"
)

logger = logging.getLogger("data-ingestion")

@app.get("/", summary="Health check")
async def root():
    return {"message": "Data Ingestion API Server is running", "version": app.version}