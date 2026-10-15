"""The AI part of the app (see backend/docs/AI.md).

embeddings.py  turns item text into vectors (local model, with a fallback)
queue.py       the ai_jobs queue: add jobs, run them in the background
semantic.py    finds the top-K similar items and stores them
runner.py      a background loop that runs queued jobs every few seconds
"""
