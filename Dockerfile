FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    BOOK_PATH=/app/book/book.json \
    DB_PATH=/app/data/players.db

WORKDIR /app

# PyMuPDF is only needed to rebuild book.json from the PDF, so the image installs just aiogram.
RUN pip install --no-cache-dir "aiogram>=3.13,<4"

# The book lives inside the image; /app/data holds only the player database (a volume).
COPY data/book.json /app/book/book.json
COPY bot.py game.py storage.py ./

RUN mkdir -p /app/data

VOLUME ["/app/data"]
CMD ["python", "bot.py"]
